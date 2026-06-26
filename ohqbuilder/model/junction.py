from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Junction:
    id: int
    name: str
    x: float | None = None
    y: float | None = None
    downstream: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
