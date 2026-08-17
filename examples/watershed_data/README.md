# Watershed-data pilot

Copy `site.example.yaml`, edit the outlet and period, then run reconnaissance:

```bash
cp examples/watershed_data/site.example.yaml sites/my_watershed.yaml
ohqbuild data validate-site --site-spec sites/my_watershed.yaml
ohqbuild data reconnaissance --site-spec sites/my_watershed.yaml \
  --output outputs/my_watershed_reconnaissance
```

Review `report.md`, choose a defensible station, and run the optional pipeline:

```bash
ohqbuild data run --site-spec sites/my_watershed.yaml \
  --station-id STATION_ID --workspace outputs/my_watershed_data \
  --export-hydropinn
```

This workflow is independent from `ohqbuild full-run`.
