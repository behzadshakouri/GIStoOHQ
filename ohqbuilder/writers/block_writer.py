from __future__ import annotations
from typing import Iterable

class BlockWriter:
    def __init__(self):
        self.lines: list[str] = []

    def line(self, text: str = "") -> None:
        self.lines.append(text)

    def comment(self, text: str) -> None:
        self.line(f"# {text}")

    def block(self, header: str, body: Iterable[str]) -> None:
        self.line(header)
        for b in body:
            self.line(f"    {b}")
        self.line("End")
        self.line()

    def text(self) -> str:
        return "\n".join(self.lines).rstrip() + "\n"
