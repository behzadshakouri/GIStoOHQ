# =============================================================================
# make_map_dem.py   (QGIS Python Console)
#
# NHA WS3 -- automated report figure: DEM (colored relief) for one site, with
# the watershed boundary drawn as an outline (no fill) over the full-extent
# raster. Assembles a print layout with title, legend, north arrow, and scale
# bar, then exports a 300-dpi PNG.
#
# This is the template figure-maker: to produce other figures (slope, CN, land
# cover, etc.) swap RASTER_PATH + the styling block and reuse everything else.
#
# USAGE (QGIS Python Console):
#   exec(open("/home/arash/Dropbox/Chloeta/NHA/PythonScripts/make_map_dem.py").read())
#
# Only ROOT and SITE are inputs; all paths are derived from them. To run a
# different site, set SITE in the console first, e.g.  SITE = "AZ12-101"
# =============================================================================

import os

from qgis.core import (
    QgsProject, QgsRasterLayer, QgsVectorLayer, QgsRectangle,
    QgsPrintLayout, QgsLayoutItemMap, QgsLayoutItemLegend,
    QgsLayoutItemScaleBar, QgsLayoutItemLabel, QgsLayoutItemPicture,
    QgsLayoutItemPolygon, QgsLayoutPoint, QgsLayoutSize, QgsUnitTypes,
    QgsLayoutExporter, QgsLayerTree,
    QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer,
    QgsFillSymbol, QgsLayoutItemMapGrid,
)
from qgis.PyQt.QtGui import QColor, QFont, QPolygonF
from qgis.PyQt.QtCore import QPointF

# ---------------------------------------------------------------------------
# --- settings (override in console before exec if needed) ------------------
# ---------------------------------------------------------------------------

try:
    ROOT
except NameError:
    ROOT = "/home/arash/Dropbox/Chloeta/NHA"

try:
    SITE
except NameError:
    SITE = "AZ12-100"

# --- everything below is derived from ROOT + SITE -------------------------
SITE_DIR = os.path.join(ROOT, "WS3_GIS", SITE)
OUT_DIR  = os.path.join(SITE_DIR, "outputs")

# DEM raster: clipped real-elevation DEM from phase 1.
RASTER_PATH   = os.path.join(OUT_DIR, "clipped", "cliped_utm_wsclip.tif")
# Watershed boundary polygon (outline only).
BOUNDARY_PATH = os.path.join(OUT_DIR, "watershed_boundary.gpkg")
# Output PNG.
OUT_PNG = os.path.join(SITE_DIR, "figures", "%s_DEM.png" % SITE.replace("-", "_"))

TITLE_TEXT  = "%s -- Digital Elevation Model" % SITE
SUBTITLE    = "NHA WS3 Flood Mitigation -- USGS 3DEP (UTM, elevations in m)"
DPI         = 300
MARGIN_FRAC = 0.02          # extent padding around the boundary (tight)
PAGE_W_MM   = 279.4         # 11 in (landscape letter)
PAGE_H_MM   = 215.9         # 8.5 in

# ---------------------------------------------------------------------------

os.makedirs(os.path.dirname(OUT_PNG), exist_ok=True)

# --- load layers -----------------------------------------------------------

if not os.path.isfile(RASTER_PATH):
    raise Exception("DEM raster not found:\n  %s" % RASTER_PATH)
if not os.path.isfile(BOUNDARY_PATH):
    raise Exception("Watershed boundary not found:\n  %s" % BOUNDARY_PATH)

rl = QgsRasterLayer(RASTER_PATH, "DEM")
if not rl.isValid():
    raise Exception("invalid raster: " + RASTER_PATH)

# boundary: support .gpkg (layername optional) or .shp
bpath = BOUNDARY_PATH
if bpath.endswith(".gpkg") and "|" not in bpath:
    # try the conventional layer name, else first layer
    test = QgsVectorLayer(bpath + "|layername=watershed_boundary", "wsb", "ogr")
    bpath = (bpath + "|layername=watershed_boundary") if test.isValid() else bpath
bl = QgsVectorLayer(bpath, "Watershed Boundary", "ogr")
if not bl.isValid():
    raise Exception("invalid boundary layer: " + BOUNDARY_PATH)

proj = QgsProject.instance()
# add raster below boundary
proj.addMapLayer(rl)
proj.addMapLayer(bl)

# --- style raster: singleband pseudocolor (colored relief) -----------------

prov = rl.dataProvider()
stats = prov.bandStatistics(1)
vmin, vmax = stats.minimumValue, stats.maximumValue
if vmin == vmax:
    vmax = vmin + 1.0
# round to whole meters at the source so no decimals reach the legend
vmin = float(int(round(vmin)))
vmax = float(int(round(vmax)))

# terrain/hypsometric ramp: low=green, rising through tan to brown, high=white
stops = [
    (0.00, QColor(56, 120, 70)),    # low  - green
    (0.30, QColor(120, 160, 85)),
    (0.55, QColor(200, 180, 120)),  # tan
    (0.80, QColor(150, 110, 75)),   # brown
    (1.00, QColor(245, 240, 235)),  # high - near white
]
items = [QgsColorRampShader.ColorRampItem(
            vmin + f * (vmax - vmin), c, "%d" % round(vmin + f * (vmax - vmin)))
         for f, c in stops]

ramp = QgsColorRampShader(vmin, vmax)
ramp.setColorRampType(QgsColorRampShader.Interpolated)
ramp.setClassificationMode(QgsColorRampShader.EqualInterval)
ramp.setColorRampItemList(items)
# integer legend labels (drop decimals like 2,270.668945 -> 2,271)
try:
    ramp.setLabelPrecision(0)
except Exception as e:
    print("  (label precision control unavailable: %s)" % e)
shader = QgsRasterShader()
shader.setRasterShaderFunction(ramp)
renderer = QgsSingleBandPseudoColorRenderer(prov, 1, shader)
# pin the renderer's classification bounds to whole numbers; the legend prints
# these values, so this removes the trailing decimals
try:
    renderer.setClassificationMin(vmin)
    renderer.setClassificationMax(vmax)
except Exception as e:
    print("  (classification min/max unavailable: %s)" % e)
rl.setRenderer(renderer)
rl.triggerRepaint()
# legend label: show "Elevation (m)" instead of "Band 1 (Gray)"
rl.setName("Elevation (m)")

# --- style boundary: outline only, no fill ---------------------------------

# build a no-brush fill symbol with a solid outline
boundary_symbol = QgsFillSymbol.createSimple({
    "color": "0,0,0,0",            # transparent fill
    "outline_color": "20,20,20,255",
    "outline_width": "0.6",
    "outline_style": "solid",
})
bl.renderer().setSymbol(boundary_symbol)
bl.triggerRepaint()

# --- compute layout extent from boundary + margin --------------------------

ext = bl.extent()
dx, dy = ext.width() * MARGIN_FRAC, ext.height() * MARGIN_FRAC
map_ext = QgsRectangle(ext.xMinimum() - dx, ext.yMinimum() - dy,
                       ext.xMaximum() + dx, ext.yMaximum() + dy)

# --- build print layout ----------------------------------------------------

mgr = proj.layoutManager()
lname = "%s_DEM" % SITE
for lay in mgr.printLayouts():
    if lay.name() == lname:
        mgr.removeLayout(lay)

layout = QgsPrintLayout(proj)
layout.initializeDefaults()
layout.setName(lname)
page = layout.pageCollection().pages()[0]
page.setPageSize(QgsLayoutSize(PAGE_W_MM, PAGE_H_MM, QgsUnitTypes.LayoutMillimeters))
mgr.addLayout(layout)

# map item -- size it to the watershed's aspect ratio so the whole basin
# fits without cropping, then zoom to the padded extent.
m = QgsLayoutItemMap(layout)
m.setBackgroundColor(QColor(255, 255, 255))

# available drawing area on the page (left block, leaving room for legend)
AVAIL_X, AVAIL_Y = 8, 20            # top-left of map area (mm)
AVAIL_W, AVAIL_H = 195, 178         # max width/height of map area (mm)

# watershed aspect ratio (from padded extent)
ext_w, ext_h = map_ext.width(), map_ext.height()
aspect = ext_w / ext_h if ext_h else 1.0

# fit a box of that aspect inside AVAIL_W x AVAIL_H
if AVAIL_W / AVAIL_H > aspect:
    # available area is wider than basin -> height-limited
    map_h = AVAIL_H
    map_w = map_h * aspect
else:
    map_w = AVAIL_W
    map_h = map_w / aspect

layout.addLayoutItem(m)
m.attemptResize(QgsLayoutSize(map_w, map_h, QgsUnitTypes.LayoutMillimeters))
m.attemptMove(QgsLayoutPoint(AVAIL_X, AVAIL_Y, QgsUnitTypes.LayoutMillimeters))
m.setExtent(map_ext)
m.zoomToExtent(map_ext)
# layer order: boundary on top of raster; lock so other project layers
# are NOT drawn in the layout/export
m.setLayers([bl, rl])
m.setKeepLayerSet(True)

# light coordinate grid; annotations drawn INSIDE the frame so they never
# collide with the title band above the map
grid = m.grid()
grid.setEnabled(True)
grid.setIntervalX((map_ext.width()) / 4.0)
grid.setIntervalY((map_ext.height()) / 4.0)
grid.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly)
grid.setAnnotationEnabled(True)
grid.setAnnotationPrecision(0)
grid.setAnnotationFontColor(QColor(0, 0, 0))
grid.setAnnotationFrameDistance(1.0)
grid.setFrameStyle(QgsLayoutItemMapGrid.NoFrame)
# put labels on bottom + right only (top/left would crowd title & legend).
# these enum-based calls vary across QGIS versions, so guard them.
try:
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll,
                              QgsLayoutItemMapGrid.Top)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll,
                              QgsLayoutItemMapGrid.Left)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll,
                              QgsLayoutItemMapGrid.Right)
    grid.setAnnotationPosition(QgsLayoutItemMapGrid.OutsideMapFrame,
                               QgsLayoutItemMapGrid.Bottom)
except Exception as e:
    print("  (grid annotation side-control skipped: %s)" % e)

# (title removed per request)

sub = QgsLayoutItemLabel(layout)
sub.setText(SUBTITLE)
fs = QFont(); fs.setPointSize(9)
sub.setFont(fs); sub.setFontColor(QColor(70, 70, 70))
layout.addLayoutItem(sub)
sub.attemptMove(QgsLayoutPoint(8, 12, QgsUnitTypes.LayoutMillimeters))
sub.attemptResize(QgsLayoutSize(200, 6, QgsUnitTypes.LayoutMillimeters))

# legend (raster ramp + boundary) -- restricted to the map's locked layers
leg = QgsLayoutItemLegend(layout)
leg.setTitle("Legend")
leg.setLinkedMap(m)
leg.setAutoUpdateModel(False)
leg.setLegendFilterByMapEnabled(True)
# rebuild the legend tree from only the two map layers
root = QgsLayerTree()
root.addLayer(bl)
rl_node = root.addLayer(rl)
leg.model().setRootGroup(root)
# hide the auto "Band 1 (Gray)" sublabel so only "Elevation (m)" shows
try:
    from qgis.core import QgsLegendStyle
    rl_node.setCustomProperty("legend/title-style", "hidden")
    # collapse the node's own label text to nothing; the ramp swatches remain
    rl_node.setName("Elevation (m)")
except Exception:
    pass
leg.adjustBoxSize()
layout.addLayoutItem(leg)
# place just to the right of the (possibly narrow) map item
leg_x = min(AVAIL_X + map_w + 4, PAGE_W_MM - 60)
leg.attemptMove(QgsLayoutPoint(leg_x, AVAIL_Y + 6, QgsUnitTypes.LayoutMillimeters))
leg.setBackgroundColor(QColor(255, 255, 255))

# scale bar
sb = QgsLayoutItemScaleBar(layout)
sb.setStyle("Single Box")
sb.setLinkedMap(m)
sb.applyDefaultSize()
sb.setUnits(QgsUnitTypes.DistanceMeters)
sb.setUnitLabel("m")
layout.addLayoutItem(sb)
sb.attemptMove(QgsLayoutPoint(AVAIL_X + 2, AVAIL_Y + map_h + 3,
                             QgsUnitTypes.LayoutMillimeters))

# north arrow: find a QGIS SVG, fall back to a drawn N+triangle
north = QgsLayoutItemPicture(layout)
svg_candidates = []
try:
    from qgis.core import QgsApplication
    pkg = QgsApplication.pkgDataPath()
    for rel in ("svg/arrows/NorthArrow_02.svg",
                "svg/arrows/NorthArrow_01.svg",
                "svg/wind_roses/WindRose_01.svg"):
        p = os.path.join(pkg, rel)
        if os.path.isfile(p):
            svg_candidates.append(p)
except Exception:
    pass

if svg_candidates:
    north.setPicturePath(svg_candidates[0])
    layout.addLayoutItem(north)
    north.attemptMove(QgsLayoutPoint(leg_x + 6, AVAIL_Y + 95,
                                     QgsUnitTypes.LayoutMillimeters))
    north.attemptResize(QgsLayoutSize(18, 22, QgsUnitTypes.LayoutMillimeters))
    north_ok = "SVG"
else:
    # drawn fallback: a filled triangle + "N" label, placed by legend column
    nx = leg_x + 10
    ny = AVAIL_Y + 95
    tri = QgsLayoutItemPolygon(layout)
    poly = QPolygonF([QPointF(nx, ny), QPointF(nx - 5, ny + 12),
                      QPointF(nx + 5, ny + 12)])
    tri.setNodes(poly)
    tri_sym = QgsFillSymbol.createSimple({"color": "20,20,20,255",
                                          "outline_color": "20,20,20,255"})
    tri.setSymbol(tri_sym)
    layout.addLayoutItem(tri)
    nlab = QgsLayoutItemLabel(layout)
    nlab.setText("N")
    nf = QFont(); nf.setPointSize(14); nf.setBold(True)
    nlab.setFont(nf); nlab.setFontColor(QColor(0, 0, 0))
    nlab.adjustSizeToText()
    layout.addLayoutItem(nlab)
    nlab.attemptMove(QgsLayoutPoint(nx - 2, ny + 12,
                                    QgsUnitTypes.LayoutMillimeters))
    north_ok = "drawn fallback (no SVG found)"

# --- export ----------------------------------------------------------------

exporter = QgsLayoutExporter(layout)
settings = QgsLayoutExporter.ImageExportSettings()
settings.dpi = DPI
settings.cropToContents = True          # trim page to the actual items
# small uniform margin around the cropped content (units: layout mm * 10? -> use
# QgsMargins in millimeters). Guarded because the field type varies by version.
try:
    from qgis.core import QgsMargins
    settings.cropMargins = QgsMargins(4, 4, 4, 4)
except Exception as _e:
    pass
res = exporter.exportToImage(OUT_PNG, settings)

if res == QgsLayoutExporter.Success:
    print("Wrote:", OUT_PNG)
    print("  raster :", RASTER_PATH)
    print("  extent : %.1f x %.1f m (boundary + %d%% margin)"
          % (map_ext.width(), map_ext.height(), int(MARGIN_FRAC * 100)))
    print("  north  :", north_ok)
    print("  dpi    :", DPI)
else:
    print("EXPORT FAILED, code:", res)