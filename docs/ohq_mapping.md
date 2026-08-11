# OHQ Mapping

| GIS/topology element | Internal object | OHQ block |
|---|---|---|
| `Subbasin_i` | `Subbasin` | Legacy CN catchment and mixed area-fraction HRU alternatives |
| `Reach_i` | `Reach` | trapezoidal channel/routing block |
| `Junction_i` | `Junction` | mixer/junction block |
| `Outlet` | `Outlet` | outlet/sink block |

Each OHQ build emits `<name>_legacy.ohq` and `<name>_mixed_hru.ohq`. The
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
