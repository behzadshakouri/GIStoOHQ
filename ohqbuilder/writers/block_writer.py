from __future__ import annotations

from typing import Any, Iterable


class BlockWriter:
    """Small command writer for the native OpenHydroQual script grammar.

    OpenHydroQual model files are command scripts.  Typical statements are:

        loadtemplate; filename=...
        addtemplate; filename=...
        create source;type=Precipitation,name=Rain,timeseries=Rain.txt
        create block;type=Catchment,name=Catchment 1,...
        create link;from=Catchment 1,to=Junction 1,type=Catchment_link,...
        setvalue; object=system, quantity=outputfile, value=output.txt

    This class deliberately does not emit the placeholder ``Project:``,
    ``Subbasin:``, ``Reach:``, ``Connect:``, or ``End`` grammar.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []

    def line(self, text: str = "") -> None:
        self.lines.append(text.rstrip())

    def comment(self, text: str) -> None:
        self.line(f"# {text}")

    @staticmethod
    def _clean_scalar(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, float):
            return f"{value:.12g}"
        return str(value).replace("\n", " ").replace("\r", " ").strip()

    @classmethod
    def _assignment(cls, key: str, value: Any) -> str:
        return f"{key}={cls._clean_scalar(value)}"

    def command(
        self,
        verb: str,
        *,
        kind: str | None = None,
        properties: Iterable[tuple[str, Any]] = (),
    ) -> None:
        head = verb if kind is None else f"{verb} {kind}"
        assignments = ",".join(
            self._assignment(key, value) for key, value in properties
        )
        if assignments:
            self.line(f"{head};{assignments}")
        else:
            self.line(f"{head};")

    def loadtemplate(self, filename: str) -> None:
        self.line(f"loadtemplate; filename={self._clean_scalar(filename)}")

    def addtemplate(self, filename: str) -> None:
        self.line(f"addtemplate; filename={self._clean_scalar(filename)}")

    def create_source(
        self,
        source_type: str,
        *,
        name: str,
        properties: Iterable[tuple[str, Any]] = (),
    ) -> None:
        values = [("type", source_type), ("name", name), *list(properties)]
        self.command("create", kind="source", properties=values)

    def create_block(
        self,
        block_type: str,
        *,
        name: str,
        properties: Iterable[tuple[str, Any]] = (),
    ) -> None:
        values = [("type", block_type), ("name", name), *list(properties)]
        self.command("create", kind="block", properties=values)

    def create_link(
        self,
        link_type: str,
        *,
        name: str,
        source: str,
        target: str,
        properties: Iterable[tuple[str, Any]] = (),
    ) -> None:
        values = [
            ("from", source),
            ("to", target),
            ("type", link_type),
            ("name", name),
            *list(properties),
        ]
        self.command("create", kind="link", properties=values)

    def setvalue(self, object_name: str, quantity: str, value: Any) -> None:
        self.line(
            "setvalue; "
            f"object={self._clean_scalar(object_name)}, "
            f"quantity={self._clean_scalar(quantity)}, "
            f"value={self._clean_scalar(value)}"
        )

    # Kept for compatibility with older callers.  It now writes the body
    # verbatim rather than inventing an ``End``-terminated pseudo-block.
    def block(self, header: str, body: Iterable[str]) -> None:
        self.line(header)
        for item in body:
            self.line(str(item))
        self.line()

    def text(self) -> str:
        return "\n".join(self.lines).rstrip() + "\n"
