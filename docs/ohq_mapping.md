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
Its `Reach_link`, `Impervious_Reach_link`, and `groundwater_to_stream` external
interfaces connect the encapsulated routing and groundwater members to the
separately generated GIS stream reach. The GIS reach network remains present in
both formulations.

Set `OHQ_RAINFALL_FILE` to a real file in OpenHydroQual's precipitation format
when building a model with assigned rainfall. If it is unset, GIStoOHQ creates
an unassigned `Rain` source instead of referencing a nonexistent default file.
