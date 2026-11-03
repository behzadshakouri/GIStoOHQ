"""Generic, provider-neutral downloaders for municipal GIS data sources.

This package holds reusable download clients (ArcGIS REST feature services,
Socrata open-data portals, ...) plus a shared manifest writer. Modules here
must stay generic: no jurisdiction-specific layer classifications, stormwater
topology, or OHQ model-building logic. That work belongs in a later,
separate stage that consumes the GeoPackages and manifests these downloaders
produce.
"""
