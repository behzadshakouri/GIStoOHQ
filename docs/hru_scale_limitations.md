# HRU runoff generation at regional subbasin scale

## Verified status

The elevation-aware snow components correctly partition precipitation, store
snow water equivalent, and release degree-day melt. In the Zarrineh/Nezamabad
example, the generated temperature offsets and maximum snow storage vary with
subbasin elevation as expected. This verifies the snow calculation; it does not
validate the downstream runoff response.

The legacy `CN_Catchment` snow result under `outputs/snowmelt_v1` is a historical
diagnostic only. Its melt entered the first routing reservoir after the
curve-number abstraction. Current GIStoOHQ versions reject that combination,
and the result must not be used as a valid simulation or cited as evidence of
spring-peak performance.

## Standard HRU limitation

`Hydrologic_Response_Unit` represents a local land column. Surface water and
infiltration compete for the same water. Direct checks against the implemented
infiltration expression matched the solved model, so the small surface runoff
is not explained by a wrong constant or disconnected port. At large aggregated
subbasin scale, the current homogeneous soil column can absorb nearly all water
before appreciable surface routing develops.

The `groundwater_to_stream` port and `soil2groundwater_unsat_link` changes do not
alter this competition. The latter belongs to `Urban_HRU`, which GIStoOHQ does
not generate, and should not be presented as a fix for the standard HRU.

## Mixed HRU limitation

`Mixed_Hydrologic_Response_Unit` has the same infiltrating pervious column plus
an impervious branch. The impervious branch correctly has no infiltration. In
the current Zarrineh GIS layer, `impervious_fraction` is populated rather than
defaulted and ranges from approximately 0.00038 to 0.01681. Its runoff dominates
because the much larger pervious branch generates almost no quick surface flow,
not because the impervious area was set to an unrealistically large fraction.

Adding infiltration to the impervious branch, increasing the mapped impervious
fraction, or tuning a physically supported saturated conductivity solely to
force more discharge would hide the structural limitation rather than resolve
it.

## Required design work

Keep the existing standard and mixed formulations stable while a separate
regional runoff formulation is researched. A defensible implementation needs
an explicit representation of subgrid heterogeneity, such as a contributing
area distribution, variable infiltration capacity, saturation-excess area, or
another reviewed regionalization method. The choice requires basin data and a
scientific basis; it must not be inferred from one hydrograph fit.

Any proposed formulation should meet these acceptance conditions:

1. Preserve water mass across rain, snow storage, melt, infiltration, surface
   runoff, evapotranspiration, groundwater storage, and outlet discharge.
2. Retain the existing local-column behavior as a documented limiting case.
3. Separate measured GIS inputs from calibrated effective parameters.
4. Reproduce hand calculations for infiltration and runoff partitioning in
   synthetic events.
5. Pass small-basin Sligo Creek and regional Zarrineh tests without relying on
   different hidden defaults.
6. Validate against event-scale precipitation, temperature, snow, and discharge
   observations before making performance claims.

Until that work is complete, snow-aware standard and mixed runs are suitable for
component and sensitivity testing. Their Zarrineh outlet discharge should be
reported as structurally limited, not as a calibrated basin simulation.
