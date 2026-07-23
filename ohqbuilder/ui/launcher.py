from __future__ import annotations

import importlib
import importlib.util
import json
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

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


def load_project_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path).expanduser()
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise LauncherError("Project config must be a mapping.")
    return data


def save_project_config(config_path: str | Path, config: dict[str, Any]) -> None:
    path = Path(config_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _set_nested(config: dict[str, Any], section: str, key: str, value: Any) -> None:
    target = config.setdefault(section, {})
    if not isinstance(target, dict):
        raise LauncherError(f"Config section is not a mapping: {section}")
    target[key] = value


def state_from_config(config_path: str | Path, config: dict[str, Any]) -> LauncherState:
    base = Path(config_path).expanduser().parent
    dem = config.get("dem_acquisition") if isinstance(config.get("dem_acquisition"), dict) else {}
    site_config = config.get("site") if isinstance(config.get("site"), dict) else {}
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}

    def path_value(value: Any) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        path = Path(value).expanduser()
        return path if path.is_absolute() else base / path

    return LauncherState(
        config_path=Path(config_path).expanduser(),
        manifest_path=path_value(dem.get("tile_manifest")),
        raw_dem_dir=path_value(paths.get("raw_dem_dir") or dem.get("raw_dem_dir")),
        root=path_value(config.get("root") or "."),
        site=str(site_config.get("name") or config.get("site") or "."),
        source_dir=path_value(config.get("download_dir") or "source_downloads"),
        target_crs=str(site_config.get("target_crs") or config.get("target_crs") or "") or None,
    )


def update_config_from_state(config: dict[str, Any], state: LauncherState) -> dict[str, Any]:
    updated = dict(config)
    _set_nested(updated, "dem_acquisition", "tile_manifest", str(state.manifest_path or ""))
    _set_nested(updated, "paths", "raw_dem_dir", str(state.raw_dem_dir or ""))
    if state.site:
        _set_nested(updated, "site", "name", state.site)
    if state.target_crs:
        _set_nested(updated, "site", "target_crs", state.target_crs)
    updated["root"] = str(state.root or ".")
    updated["download_dir"] = str(state.source_dir or "source_downloads")
    return updated


def geojson_preview_summary(path: str | Path) -> str:
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise LauncherError("Preview file must be a GeoJSON FeatureCollection.")
    geometry_types = sorted({
        feature.get("geometry", {}).get("type", "Unknown")
        for feature in features
        if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
    })
    return f"{len(features)} feature(s); geometry: {', '.join(geometry_types) or 'none'}"


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
        tk.Button(buttons, text="load config", command=self.load_config).pack(side="left")
        tk.Button(buttons, text="save config", command=self.save_config).pack(side="left")
        tk.Button(buttons, text="preview acquisition", command=self.preview_acquisition).pack(side="left")
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


    def apply_state(self, state: LauncherState) -> None:
        self.config_var.set(str(state.config_path))
        self.manifest_var.set(str(state.manifest_path or ""))
        self.raw_dem_var.set(str(state.raw_dem_dir or ""))
        self.root_var.set(str(state.root or "."))
        self.site_var.set(state.site or "")
        self.source_var.set(str(state.source_dir or ""))
        self.crs_var.set(state.target_crs or "")

    def load_config(self) -> None:
        try:
            config = load_project_config(self.config_var.get())
            self.apply_state(state_from_config(self.config_var.get(), config))
            self.messages.put("Loaded config.\n")
        except (OSError, LauncherError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.messages.put(f"ERROR: {exc}\n")

    def save_config(self) -> None:
        try:
            current = load_project_config(self.config_var.get()) if Path(self.config_var.get()).exists() else {}
            save_project_config(self.config_var.get(), update_config_from_state(current, self.state()))
            self.messages.put("Saved config.\n")
        except (OSError, LauncherError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.messages.put(f"ERROR: {exc}\n")

    def preview_acquisition(self) -> None:
        try:
            config = load_project_config(self.config_var.get())
            dem = config.get("dem_acquisition") if isinstance(config.get("dem_acquisition"), dict) else {}
            area = dem.get("acquisition_area")
            if not isinstance(area, str) or not area:
                raise LauncherError("dem_acquisition.acquisition_area is not configured.")
            path = Path(area).expanduser()
            if not path.is_absolute():
                path = Path(self.config_var.get()).expanduser().parent / path
            self.messages.put(f"Acquisition preview: {geojson_preview_summary(path)}\n")
        except (OSError, LauncherError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            self.messages.put(f"ERROR: {exc}\n")

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
