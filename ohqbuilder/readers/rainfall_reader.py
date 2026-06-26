from __future__ import annotations
import csv
from pathlib import Path

class Atlas14Reader:
    def read_frequency_table(self, path: Path) -> dict[str, dict[str, float]]:
        out = {}
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                dur = str(row.get("duration", "")).strip()
                if not dur:
                    continue
                out[dur] = {}
                for k, v in row.items():
                    if k == "duration":
                        continue
                    try:
                        out[dur][k.strip()] = float(v)
                    except (TypeError, ValueError):
                        pass
        return out
