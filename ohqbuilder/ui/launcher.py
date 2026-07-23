from __future__ import annotations

import importlib
import importlib.util
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

WorkflowStep = Literal[
    "prepare-dem",
    "download-dem-manifest",
    "materialize-inputs",
    "validate-dem",
]


class LauncherError(RuntimeError):
    """Raised when the lightweight UI launcher cannot be started."""


@dataclass(frozen=True)
class WorkflowCommand:
    label: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class LauncherState:
    config_path: Path
    manifest_path: Path | None = None
    raw_dem_dir: Path | None = None
    root: Path | None = None
    site: str | None = None
    source_dir: Path | None = None
    target_crs: str | None = None


def command_for_step(step: WorkflowStep, state: LauncherState) -> WorkflowCommand:
    """Build the backend command that the UI should execute for a workflow step."""

    if step == "prepare-dem":
        return WorkflowCommand("Prepare DEM", ("ohqbuild", "prepare-dem", "--config", str(state.config_path)))
    if step == "validate-dem":
        return WorkflowCommand("Validate DEM", ("ohqbuild", "validate-dem", "--config", str(state.config_path)))
    if step == "download-dem-manifest":
        if state.manifest_path is None or state.raw_dem_dir is None:
            raise LauncherError("Manifest path and raw DEM directory are required for DEM download.")
        return WorkflowCommand(
            "Download DEM Tiles",
            (
                "ohqbuild",
                "download-dem-manifest",
                "--manifest",
                str(state.manifest_path),
                "--out-dir",
                str(state.raw_dem_dir),
            ),
        )
    if step == "materialize-inputs":
        if state.root is None or not state.site:
            raise LauncherError("Root and site are required for materialize-inputs.")
        argv = ["ohqbuild", "materialize-inputs", "--root", str(state.root), "--site", state.site]
        if state.source_dir is not None:
            argv.extend(("--source-dir", str(state.source_dir)))
        if state.target_crs:
            argv.extend(("--target-crs", state.target_crs))
        if state.manifest_path is not None:
            argv.extend(("--dem-manifest", str(state.manifest_path)))
        return WorkflowCommand("Materialize Inputs", tuple(argv))
    raise LauncherError(f"Unsupported workflow step: {step}")


def _require_tkinter():
    if importlib.util.find_spec("tkinter") is None:
        raise LauncherError("tkinter is not available in this Python environment.")
    return importlib.import_module("tkinter")


class CommandRunner(threading.Thread):
    def __init__(self, command: WorkflowCommand, messages: queue.Queue[str]):
        super().__init__(daemon=True)
        self.command = command
        self.messages = messages

    def run(self) -> None:
        self.messages.put(f"$ {' '.join(self.command.argv)}\n")
        process = subprocess.Popen(
            self.command.argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert process.stdout is not None
        for line in process.stdout:
            self.messages.put(line)
        status = process.wait()
        self.messages.put(f"\n[{self.command.label} exited with {status}]\n")


class LauncherApp:
    """Small Tk-based launcher that writes no workflow logic of its own."""

    def __init__(self) -> None:
        tk = _require_tkinter()
        self.tk = tk
        self.root = tk.Tk()
        self.root.title("GIStoOHQ DEM Workflow Launcher")
        self.messages: queue.Queue[str] = queue.Queue()
        self.config_var = tk.StringVar(value="config.example.json")
        self.manifest_var = tk.StringVar(value="intermediate/dem_download_manifest.json")
        self.raw_dem_var = tk.StringVar(value="dem/raw")
        self.root_var = tk.StringVar(value=".")
        self.site_var = tk.StringVar(value=".")
        self.source_var = tk.StringVar(value="source_downloads")
        self.crs_var = tk.StringVar(value="")
        self._build()
        self._poll_messages()

    def _build(self) -> None:
        tk = self.tk
        frame = tk.Frame(self.root, padx=10, pady=10)
        frame.pack(fill="both", expand=True)
        rows = [
            ("Config", self.config_var),
            ("Manifest", self.manifest_var),
            ("Raw DEM dir", self.raw_dem_var),
            ("Root", self.root_var),
            ("Site", self.site_var),
            ("Source dir", self.source_var),
            ("Target CRS", self.crs_var),
        ]
        for row, (label, variable) in enumerate(rows):
            tk.Label(frame, text=label).grid(row=row, column=0, sticky="w")
            tk.Entry(frame, textvariable=variable, width=70).grid(row=row, column=1, sticky="ew")
        buttons = tk.Frame(frame)
        buttons.grid(row=len(rows), column=0, columnspan=2, sticky="ew", pady=8)
        for step in ("prepare-dem", "download-dem-manifest", "materialize-inputs", "validate-dem"):
            tk.Button(buttons, text=step, command=lambda value=step: self.run_step(value)).pack(side="left")
        self.log = tk.Text(frame, height=24, width=100)
        self.log.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(len(rows) + 1, weight=1)

    def state(self) -> LauncherState:
        crs = self.crs_var.get().strip() or None
        return LauncherState(
            config_path=Path(self.config_var.get()).expanduser(),
            manifest_path=Path(self.manifest_var.get()).expanduser(),
            raw_dem_dir=Path(self.raw_dem_var.get()).expanduser(),
            root=Path(self.root_var.get()).expanduser(),
            site=self.site_var.get().strip() or None,
            source_dir=Path(self.source_var.get()).expanduser(),
            target_crs=crs,
        )

    def run_step(self, step: WorkflowStep) -> None:
        try:
            command = command_for_step(step, self.state())
        except LauncherError as exc:
            self.messages.put(f"ERROR: {exc}\n")
            return
        CommandRunner(command, self.messages).start()

    def _poll_messages(self) -> None:
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            self.log.insert("end", message)
            self.log.see("end")
        self.root.after(100, self._poll_messages)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    try:
        LauncherApp().run()
    except LauncherError as exc:
        print(f"ui failed: {exc}")
        return 2
    return 0
