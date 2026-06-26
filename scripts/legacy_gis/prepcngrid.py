# =============================================================================
# prep_cn_grid.py   (QGIS Python Console)
#
# Step 1 of the curve-number workflow: coregister the land-cover and HSG rasters
# onto the DEM grid so they can be combined cell-for-cell in build_cn_raster.py.
#
# The three clipped inputs arrive on three different grids and CRSs:
#   DEM        cliped_utm_wsclip.tif        EPSG:26912 (UTM 12N)  <- GRID TEMPLATE
#   land cover nlcd_*_wsclip.tif            EPSG:5070  (Albers)
#   HSG        hsg_wsclip.tif               EPSG:4326  (geographic)
#
# The DEM grid is the target: delineation, subwatersheds, and pour points are all
# in UTM 12N, so aligning the CN inputs to the DEM keeps the later zonal step in
# the same CRS as the subwatershed zones, with no reprojection.
#
# Both inputs are CATEGORICAL (NLCD class codes; HSG codes 1-4), so resampling is
# NEAREST-NEIGHBOUR only -- bilinear/cubic would invent class codes. Outputs share
# the DEM's exact CRS, extent, size, and pixel: pixel-aligned with the DEM and
# with each other.
#
# Writes into <SITE>/outputs/clipped/ :
#   landcover_aligned.tif   land cover on the DEM grid
#   hsg_aligned.tif         HSG on the DEM grid
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
import glob
from osgeo import gdal
from qgis.core import QgsProject, QgsRasterLayer
gdal.UseExceptions()

# --- settings (set ROOT + SITE_DIR ONCE) -----------------------------------
try:
    ROOT
except NameError:
    ROOT = "C:/Users/smnfa/Dropbox/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

DEM_NAME = "cliped_utm_wsclip.tif"   # grid template (its CRS/extent/grid win)
HSG_NAME = "hsg_wsclip.tif"          # resampled onto the template
LC_GLOB  = "nlcd_*_wsclip.tif"       # land cover (name carries the site id)

OUT_LC   = "landcover_aligned.tif"
OUT_HSG  = "hsg_aligned.tif"

ADD_TO_PROJECT = True
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
CLIP_DIR  = os.path.join(site_path, "outputs", "clipped")

dem = os.path.join(CLIP_DIR, DEM_NAME)
hsg = os.path.join(CLIP_DIR, HSG_NAME)

print("Site    :", site_path)
print("Clipped :", CLIP_DIR)

if not os.path.isfile(dem):
    raise Exception("DEM template not found: " + dem)
if not os.path.isfile(hsg):
    raise Exception("HSG raster not found: " + hsg)

# resolve land cover by glob, excluding our own output if re-run
lc_hits = [p for p in sorted(glob.glob(os.path.join(CLIP_DIR, LC_GLOB)))
           if os.path.basename(p) != OUT_LC]
if not lc_hits:
    raise Exception("land cover (%s) not found in %s" % (LC_GLOB, CLIP_DIR))
lc = lc_hits[0]


def grid_of(path):
    ds = gdal.Open(path)
    g = (ds.GetProjection(), ds.GetGeoTransform(),
         ds.RasterXSize, ds.RasterYSize)
    ds = None
    return g


def bounds_from(gt, nx, ny):
    minx = gt[0]; maxy = gt[3]
    maxx = minx + gt[1] * nx
    miny = maxy + gt[5] * ny
    return (minx, miny, maxx, maxy)


def align_to_template(src, dst, srs, bounds, nx, ny, nodata=0):
    if os.path.exists(dst):
        os.remove(dst)
    gdal.Warp(
        dst, src,
        options=gdal.WarpOptions(
            dstSRS=srs,
            outputBounds=bounds,          # (minx, miny, maxx, maxy) in dst CRS
            width=nx, height=ny,          # exact pixel grid of the template
            resampleAlg="near",           # categorical -> nearest only
            srcNodata=nodata, dstNodata=nodata,
            outputType=gdal.GDT_Byte,
            creationOptions=["COMPRESS=LZW"],
        ),
    )


# --- template grid from the DEM --------------------------------------------
tmpl_srs, gt, nx, ny = grid_of(dem)
tmpl_bounds = bounds_from(gt, nx, ny)
print("Template (DEM): %s" % DEM_NAME)
print("  %d x %d  pixel %.3f x %.3f" % (nx, ny, gt[1], -gt[5]))
print("Land cover    : %s" % os.path.basename(lc))
print("HSG           : %s" % HSG_NAME)

out_lc  = os.path.join(CLIP_DIR, OUT_LC)
out_hsg = os.path.join(CLIP_DIR, OUT_HSG)
align_to_template(lc,  out_lc,  tmpl_srs, tmpl_bounds, nx, ny, nodata=0)
align_to_template(hsg, out_hsg, tmpl_srs, tmpl_bounds, nx, ny, nodata=0)

# --- verify all three now share grid ---------------------------------------
_, gt_lc,  nx_lc,  ny_lc  = grid_of(out_lc)
_, gt_hsg, nx_hsg, ny_hsg = grid_of(out_hsg)
same = (nx_lc == nx == nx_hsg and ny_lc == ny == ny_hsg
        and gt_lc == gt and gt_hsg == gt)

print("\nWrote:")
print("  %s  (%d x %d)" % (OUT_LC, nx_lc, ny_lc))
print("  %s  (%d x %d)" % (OUT_HSG, nx_hsg, ny_hsg))
print("Grid match with DEM:", "YES" if same else "NO -- check inputs")

# --- load into the QGIS project --------------------------------------------
if ADD_TO_PROJECT:
    for path, name in [(out_lc, "landcover_aligned"), (out_hsg, "hsg_aligned")]:
        rl = QgsRasterLayer(path, name)
        if rl.isValid():
            QgsProject.instance().addMapLayer(rl)
            print("  added to project:", name)
        else:
            print("  could not load:", path)

print("\nDone.")
