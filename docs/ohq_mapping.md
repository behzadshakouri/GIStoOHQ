# OHQ Mapping

| GIS/topology element | Internal object | OHQ block |
|---|---|---|
| `Subbasin_i` | `Subbasin` | Legacy CN catchment and mixed area-fraction HRU alternatives |
| `Reach_i` | `Reach` | trapezoidal channel/routing block |
| `Junction_i` | `Junction` | mixer/junction block |
| `Outlet` | `Outlet` | outlet/sink block |

Each OHQ build emits `<name>_legacy.ohq`, `<name>_mixed_hru.ohq`, and
`<name>_standard_hru.ohq`. The
legacy model retains the curve-number-derived runoff coefficient. The mixed
HRU model reads a GIS impervious fraction (or percent) when available and
partitions the subbasin between infiltrating and impervious catchments; when
the input has no impervious field, the composite's 0.2 default is used.
The GIS preparation phase writes `surface_elevation_m` as the subbasin mean DEM
elevation and estimates `impervious_fraction` as the area-weighted midpoint of
the NLCD developed-class impervious ranges (classes 21 through 24).
Its `Reach_link` and `Impervious_Reach_link` interface labels expose the two
encapsulated routing members, which use registered `Trapezoidal_Channel_link`
connectors to the separately generated GIS stream reach. The registered
`groundwater_to_stream` connector carries baseflow. The GIS reach network
remains present in both formulations.

OpenHydroQual's composite criteria parser accepts one comparison per `criteria`
expression. The mixed-HRU resource must therefore express the strict fraction
range as `impervious_fraction*(1-impervious_fraction)>0`, not
`impervious_fraction>0&impervious_fraction<1`; the latter is interpreted as a
single property name. Other relational ranges should likewise use one
comparison, for example
`(initial_moisture_content-theta_res)*(theta_sat-initial_moisture_content)>=0`.

Set `OHQ_RAINFALL_FILE` to a real file in OpenHydroQual's precipitation format
when building a model with assigned rainfall. If it is unset, GIStoOHQ creates
an unassigned `Rain` source instead of referencing a nonexistent default file.

To apply ET, set `OHQ_ET_FILE` to an existing OpenHydroQual time-series CSV
containing nonnegative **depth rates in m/day** (time in OpenHydroQual days in
the first column, rate in the second). The writer loads
`soil_evapotranspiration_models.json`, creates an
`Evapotranspiration_Time_Series (Soil)` source with `ET_timeseries=...`, and
assigns it to each standard and mixed HRU. OpenHydroQual applies this source
to `Soil_1` and removes water using its corrected negative rate. Without this
setting the HRU ET property remains unassigned. The legacy `CN_Catchment`
output does not receive ET.

The separately downloaded NASA POWER `EVPTRNS` asset is **not** assigned to
`OHQ_ET_FILE` automatically. It may have units of `MJ/m²/day`, which require
an explicit latent-heat conversion, or `mm/day`, which must be divided by
1000 to obtain m/day. Preserve the actual time axis when preparing the OHQ
CSV; the data acquisition package does not convert or upsample the daily ET.
The standard HRU output requires the OpenHydroQual
`Hydrologic_Response_Unit` template with the `groundwater_to_stream` external
port (added alongside this writer feature) to route groundwater into the GIS
reach network.

## Optional DEM-derived channel segments

Set `OHQ_CHANNEL_PROFILES_FILE` to a reviewed, long-format station-elevation
CSV to emit a fourth file, `<name>_detailed_mixed_hru.ohq`, and an accompanying
`<name>_channel_fit.csv` review table. The three existing outputs remain
available. The detailed output uses existing OpenHydroQual trapezoidal channel
blocks and `Trapezoidal_Channel_link` connections; no new OHQ template is
needed. This first implementation applies to the mixed HRU formulation.

The CSV needs these columns (one row per elevation sample):

| Column | Meaning |
|---|---|
| `reach_id` | ID in GIStoOHQ's `reaches.gpkg` |
| `station_m` | Distance downstream along that reach, from 0 to `length_m` |
| `offset_m`, `elev_m` | Sample offset across the section and DEM elevation in metres |
| `bank_left_m`, `bank_right_m` | Reviewed bank offsets, repeated on every sample of a section; left negative and right positive |

GIStoOHQ's legacy `rascutlines.py` and `rasxsprofiles.py` already generate
transects and DEM profiles for HEC-RAS. Their `xs_profiles.csv` must be mapped
to the corresponding `reach_id`, with stations measured separately along each
reach and bank offsets reviewed, before use here. The detailed writer does not
guess banks from a terrain raster. At least five unique offsets are needed per
profile; profiles with less than 0.25 m bank-to-bed relief are rejected because
they may represent a hydroflattened water surface.

For multiple reaches, generate a candidate CSV directly from the existing
`reaches.gpkg` and an uncarved bare-earth DEM in the same metre-based projected
CRS:

```bash
ohqbuild sample-channel-profiles --reaches outputs/reaches.gpkg \
  --dem outputs/cliped_utm_wsclip.tif --output outputs/channel_profiles.csv \
  --spacing-m 50 --half-width-m 30 --sample-step-m 1
```

The command uses each reach centerline and samples perpendicular DEM transects;
it leaves `bank_left_m` and `bank_right_m` blank on purpose. Inspect the
transects, fill both bank offsets for every row of each chosen section, and
remove sections that cross a bridge, confluence, missing DEM area, or a
hydroflattened channel. Then set
`OHQ_CHANNEL_PROFILES_FILE=outputs/channel_profiles.csv` for `ohqbuild build` or
`ohqbuild run`. The sample step should reflect the DEM resolution; a coarse
DEM cannot recover a narrow channel by taking more samples from the same cells.

Each profile fits one trapezoid by matching DEM wetted area at five depths.
The reach is divided at midpoints between profile stations, so each resulting
segment represents its nearest profile and has a length that contributes to
the original reach's total length. The model invert follows the original GIS
upstream and downstream bed elevations, interpolated along the reach; the
sampled DEM minimum is written separately to the review table. A mapped
subbasin outfall on the reach centerline selects its receiving segment. When
no reliable outfall coordinate exists, all three mixed-HRU outflows attach to
the downstream segment, with a comment in the OHQ file. GIS reaches without
profiles retain their existing single trapezoidal block.

The fit table gives the width, side slope, area-fit RMSE, DEM minimum and
assigned OHQ invert for every new segment, with a `DEM_ONLY_UNVERIFIED`
bathymetry flag. Review the transects, vertical datum, banks, segment
connectivity, and discharges before quantitative use. Standard topographic
lidar often measures the water surface rather than the submerged bed;
surveyed bathymetry is needed to verify underwater geometry. A low area-fit
error alone does not establish bed accuracy.
