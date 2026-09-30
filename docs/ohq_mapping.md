# OHQ Mapping

| GIS/topology element | Internal object | OHQ block |
|---|---|---|
| `Subbasin_i` | `Subbasin` | Legacy CN catchment and mixed area-fraction HRU alternatives |
| `Reach_i` | `Reach` | trapezoidal channel/routing block |
| `Junction_i` | `Junction` | mixer/junction block |
| `Outlet` | `Outlet` | outlet/sink block |

Each rain-only OHQ build emits `<name>_legacy.ohq`, `<name>_mixed_hru.ohq`, and
`<name>_standard_hru.ohq`. A snow-enabled build emits the mixed and standard
files, skips the unsupported legacy file, and removes a stale legacy output at
the same target path. The
legacy model retains the curve-number-derived runoff coefficient. The mixed
HRU model reads a GIS impervious fraction (or percent) when available and
partitions the subbasin between infiltrating and impervious catchments; when
the input has no impervious field, the composite's 0.2 default is used.
The GIS preparation phase writes `surface_elevation_m` as the subbasin mean DEM
elevation and estimates `impervious_fraction` as the area-weighted midpoint of
the NLCD developed-class impervious ranges (classes 21 through 24).
Its `Reach_link` and `Impervious_Reach_link` interface labels expose the two
encapsulated routing members, which use registered `Trapezoidal_Channel_link`
connectors to the separately generated GIS stream reach. By default, the
registered `groundwater_to_stream` Darcy connector carries baseflow. The GIS
reach network remains present in both formulations.

Set `OHQ_BASEFLOW_METHOD=linear_reservoir` to replace that Darcy connector with
OpenHydroQual's `Linear_baseflow` connector in the standard and mixed HRU
outputs. GIStoOHQ emits exactly one baseflow connector from each HRU to its
receiving reach; the methods are alternatives and are never added in parallel.
The linear option uses
`Q = k max(moisture_content - min_moisture_content, 0) volume`, where `k` is a
first-order recession rate. Configure it with
`OHQ_BASEFLOW_RECESSION_RATE_PER_DAY` (default `0.01`) and
`OHQ_BASEFLOW_MIN_MOISTURE_CONTENT` (default `0.3`). The threshold is an
absolute volumetric moisture content and must not exceed the groundwater
porosity if the aquifer is expected to produce baseflow. The standard HRU's
default initial groundwater moisture content is `0.25`, so the default `0.3`
threshold intentionally starts that formulation with no active baseflow
storage; set and calibrate both values for the basin rather than interpreting
the defaults as site measurements.

This option changes only groundwater-to-stream discharge. Recharge enters the
groundwater store once through the HRU's existing soil-to-groundwater link, and
the chosen baseflow connector removes it once through the shared mass-balance
solver. It does not repair or bypass the separate surface-runoff and
infiltration limitations described below.

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

Set `OHQ_TEMPERATURE_FILE` to an OpenHydroQual two-column air-temperature time
series (model time, degrees Celsius) to enable snow accumulation and melt. The
writer then loads `snowmelt.json`, partitions `OHQ_RAINFALL_FILE` into
complementary liquid-rain and snowfall sources, creates a stateful snowpack for
each modeled surface, and routes storage-limited degree-day melt into the
catchment. Mixed HRUs receive separate pervious and impervious snowpacks whose
areas sum to the GIS subbasin area. The default transition is -1 to 1 °C and
the default degree-day factor is 0.003 m/day/°C; edit or calibrate those values
where local snow observations support different parameters. When
`OHQ_TEMPERATURE_FILE` is unset, the existing direct-rainfall model is emitted.
Correct snow accumulation does not by itself validate HRU runoff generation;
see [HRU runoff generation at regional subbasin scale](hru_scale_limitations.md)
for the verified standard and mixed HRU limitations.

Snow generation is supported for `standard_hru` and `mixed_hru`. GIStoOHQ
rejects the legacy `CN_Catchment` formulation when temperature forcing is
enabled. Its generic external inflow enters the first routing reservoir after
the curve-number abstraction, so attaching snowmelt there would bypass the CN
loss calculation and overstate runoff. A dedicated CN melt partition is needed
before that combination can be modeled correctly.

If the temperature series represents a station or gridded cell at a known
elevation, set `OHQ_TEMPERATURE_REFERENCE_ELEVATION_M` to that elevation.
GIStoOHQ then creates a separate rain/snow partition for each subbasin and
applies
`offset = lapse_rate * (subbasin_elevation - reference_elevation) / 1000`.
The default environmental lapse rate is -6.5 °C/km; override it with
`OHQ_TEMPERATURE_LAPSE_RATE_C_PER_KM` when local evidence supports another
value. A lapse rate without a reference elevation is rejected. If the reference
elevation is omitted, the offset is zero everywhere rather than assuming sea
level. When a reference elevation is supplied, every subbasin must have a
finite GIS elevation; the writer rejects missing elevations instead of treating
them as zero.

Rainfall and temperature files must use the same model-time origin and cover
the complete simulation window. Preserve the source, location, elevation,
units, time zone, missing-data treatment, and aggregation method for both
forcings. Observed or otherwise defensible event-scale series are required for
calibration and validation. A smoothed climatology or synthetic temperature
series can support a sensitivity test, but its resulting peak timing is not
evidence that the model reproduced an observed snowmelt event.

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
