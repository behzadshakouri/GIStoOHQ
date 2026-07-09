#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

from ohqbuilder.soil_retrieval import SoilRetrievalError, retrieve_soil_texture


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieve USDA SDA soil texture layers for a GIStoOHQ site.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--site", default=".")
    parser.add_argument("--buffer", type=float, default=5000.0)
    parser.add_argument("--pixel-size", type=float, default=0.0003)
    parser.add_argument("--top-depth", type=float, default=30.0)
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    try:
        result = retrieve_soil_texture(
            root,
            args.site,
            buffer=args.buffer,
            pixel_size=args.pixel_size,
            top_depth=args.top_depth,
        )
    except SoilRetrievalError as exc:
        print(f"retrieve_soil_texture failed: {exc}")
        return 2
    print(f"Wrote {result.vector_path}")
    for raster in result.raster_paths:
        print(f"Wrote {raster}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
