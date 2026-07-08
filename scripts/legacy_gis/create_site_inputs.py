#!/usr/bin/env python3
"""
create_site_inputs.py

Creates the minimum directory structure expected by the Phase 2
CN-processing scripts.

Usage:
    python create_site_inputs.py <SITE_DIR>

Example:
    python create_site_inputs.py examples/AZ12-100
"""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    print("Usage: python create_site_inputs.py <SITE_DIR>")
    sys.exit(1)

site = Path(sys.argv[1]).resolve()

if not site.exists():
    print(f"ERROR: Site directory not found:\n  {site}")
    sys.exit(1)

landcover = site / "landcover"
soils = site / "soils"

landcover.mkdir(parents=True, exist_ok=True)
soils.mkdir(parents=True, exist_ok=True)

# README files
lc_readme = landcover / "README.txt"
if not lc_readme.exists():
    lc_readme.write_text(
"""Place the NLCD raster here.

Expected filename:
    nlcd_2023_{site}.tif
""".format(site=site.name))

soil_readme = soils / "README.txt"
if not soil_readme.exists():
    soil_readme.write_text(
"""Place the hydrologic soil datasets here.

Expected files:
    hsg.tif
    hydrologic_soil_groups.gpkg
""")

print()
print("Created (or verified):")
print(f"  {landcover}")
print(f"  {soils}")
print()
print("Expected inputs:")
print(f"  {landcover / ('nlcd_2023_' + site.name + '.tif')}")
print(f"  {soils / 'hsg.tif'}")
print(f"  {soils / 'hydrologic_soil_groups.gpkg'}")
print()
print("Done.")
