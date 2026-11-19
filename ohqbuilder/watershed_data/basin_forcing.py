"""Reproducible AORC rainfall + gridMET reference-ET acquisition over a polygon.

Weather only: no discharge, normalization, splits or model fitting. Raw replies
and hashes are retained, partial downloads resumable only for identical inputs.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

from .schemas import SiteSpec, WatershedDataError, canonical_json

AORC = 'https://noaa-nws-aorc-v1-1-1km.s3.amazonaws.com'
GRIDMET = 'https://www.reacchpna.org/thredds/ncss/grid/agg_met_pet_1979_CurrentYear_CONUS.nc'
ET_VARIABLE = 'daily_mean_reference_evapotranspiration_grass'


def basin_forcing_arguments(site_spec, basin, output, *, station_id, max_download_mb=512, resume=False):
    if not all(isinstance(v,str) and v.strip() for v in [site_spec,basin,output]):
        raise WatershedDataError('Choose a SiteSpec, gauge basin polygon and new weather folder')
    if not isinstance(station_id,str) or not re.fullmatch(r'[0-9]{8,15}',station_id):
        raise WatershedDataError('Select an explicit USGS station for the gauge-basin handoff')
    if isinstance(max_download_mb,bool) or not math.isfinite(float(max_download_mb)) or float(max_download_mb)<=0:
        raise WatershedDataError('A positive finite weather download limit is required')
    args=['ohqbuild','data','basin-forcing','--site-spec',site_spec,'--basin',basin,'--output',output,
          '--station-id',station_id,'--max-download-mb',str(float(max_download_mb))]
    return args+(['--resume'] if resume else [])


def grid_weights(basin, lats, lons):
    """Cell intersection areas, with full polygon coverage required (EPSG5070)."""
    import numpy as np
    from pyproj import Transformer
    from shapely.geometry import box
    from shapely.ops import transform
    axes = [np.asarray(a, dtype=float) for a in (lats, lons)]
    for a in axes:
        delta = np.diff(a)
        if (len(a) < 2 or not np.isfinite(a).all() or not (np.all(delta > 0) or np.all(delta < 0))
                or not np.allclose(delta, delta[0], rtol=1e-5, atol=1e-8)):
            raise WatershedDataError('Finite regular monotone provider grid required')
    project = Transformer.from_crs(4326, 5070, always_xy=True).transform
    projected = transform(project, basin)
    if projected.is_empty or not projected.is_valid or projected.area <= 0:
        raise WatershedDataError('Valid nonempty basin polygon required')
    dy, dx = abs(axes[0][1]-axes[0][0])/2, abs(axes[1][1]-axes[1][0])/2
    xmin, ymin, xmax, ymax = basin.bounds
    cells = []
    for i in np.flatnonzero((axes[0]+dy >= ymin) & (axes[0]-dy <= ymax)):
        for j in np.flatnonzero((axes[1]+dx >= xmin) & (axes[1]-dx <= xmax)):
            cell = transform(project, box(axes[1][j]-dx, axes[0][i]-dy, axes[1][j]+dx, axes[0][i]+dy))
            area = projected.intersection(cell).area
            if area > 0:
                cells.append((int(i), int(j), float(area)))
    covered = math.fsum(c[2] for c in cells)
    if not cells or abs(covered/projected.area-1) > 1e-5:
        raise WatershedDataError('Provider grid does not cover the entire basin')
    return cells


def weighted_value(values, weights):
    """Require all weighted cells; missing rainfall must never become zero rain."""
    import numpy as np
    values, weights = np.asarray(values, dtype=float), np.asarray(weights, dtype=float)
    if values.shape != weights.shape or values.ndim != 1 or not len(values) or not np.isfinite(weights).all() or np.any(weights <= 0):
        raise WatershedDataError('Positive finite cell weights required')
    valid = np.isfinite(values) & (values >= 0)
    coverage = float(weights[valid].sum()/weights.sum())
    if not valid.all():
        return 0., False, coverage
    mean = float(np.dot(values, weights/weights.sum()))
    if not math.isfinite(mean):
        raise WatershedDataError('Weighted forcing overflow')
    return mean, True, coverage


class RawCache:
    def __init__(self, root, budget_mb, progress):
        self.root = Path(root)
        self.path = self.root/'raw_sources.json'
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else {}
        if not math.isfinite(budget_mb) or budget_mb <= 0:
            raise WatershedDataError('Positive finite download budget required')
        self.budget = int(budget_mb*1024**2)
        self.progress = progress

    def get(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.root/'raw'/key
        if key in self.entries:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != self.entries[key]['sha256']:
                raise WatershedDataError('Cached source hash mismatch')
            return data
        size = sum(e['bytes'] for e in self.entries.values())
        if size >= self.budget:
            raise WatershedDataError('Weather acquisition byte budget exceeded')
        request = urllib.request.Request(url, headers={'User-Agent': 'GIStoOHQ-basin-forcing/1'})
        with urllib.request.urlopen(request, timeout=60) as reply:
            data = reply.read(min(32*1024**2, self.budget-size)+1)
        if len(data) > 32*1024**2 or size+len(data) > self.budget:
            raise WatershedDataError('Weather acquisition byte budget exceeded')
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != data:
            raise WatershedDataError('Refusing to replace a retained raw source')
        path.write_bytes(data)
        self.entries[key] = {'url': url, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        temporary = self.path.with_suffix('.tmp')
        temporary.write_bytes(canonical_json(self.entries))
        temporary.replace(self.path)
        return data


def zarr_chunk(cache, base, metadata, variable, indices):
    """Decode only the declared simple Zstd Zarr-v2 encoding, never guess."""
    import numpy as np
    spec = metadata[variable+'/.zarray']
    if (spec.get('zarr_format') != 2 or spec.get('filters') is not None
            or spec.get('order') != 'C' or spec.get('compressor', {}).get('id') != 'zstd'):
        raise WatershedDataError('Unsupported provider Zarr encoding')
    raw = cache.get(base+'/'+variable+'/'+'.'.join(map(str, indices)))
    decoded = subprocess.run(['zstd', '-q', '-d', '-c'], input=raw, stdout=subprocess.PIPE, check=True).stdout
    expected = math.prod(spec['chunks'])*np.dtype(spec['dtype']).itemsize
    if len(decoded) != expected:
        raise WatershedDataError('Provider chunk byte count mismatch')
    return np.frombuffer(decoded, dtype=spec['dtype']).reshape(spec['chunks'])


def zarr_axis(cache, base, metadata, variable):
    import numpy as np
    spec = metadata[variable+'/.zarray']
    if len(spec['shape']) != 1:
        raise WatershedDataError('Expected one-dimensional provider coordinate')
    chunks = [zarr_chunk(cache, base, metadata, variable, [i]) for i in range(math.ceil(spec['shape'][0]/spec['chunks'][0]))]
    return np.concatenate(chunks)[:spec['shape'][0]]


def acquire_basin_forcing(site_spec, basin_path, output, *, resume=False, max_download_mb=512, progress=print, station_id=""):
    import geopandas as gpd
    import numpy as np
    from netCDF4 import Dataset, num2date
    from shapely.ops import unary_union
    from shapely.geometry import Point, mapping
    from pyproj import Transformer
    from shapely.ops import transform

    spec = SiteSpec.from_file(site_spec)
    start = datetime.fromisoformat(spec.study_start.replace('Z', '+00:00'))
    end = datetime.fromisoformat(spec.study_end.replace('Z', '+00:00')) + timedelta(hours=1)
    if (start.minute or start.second or end.minute or end.second or
            not start.year >= 1979 or (end-start).days > 3660 or max_download_mb <= 0):
        raise WatershedDataError('Bounded whole-hour weather period required (maximum ten years)')
    source_station=spec.sources.get('discharge',{}).get('station_id','')
    if station_id and (not re.fullmatch(r'[0-9]{8,15}',station_id) or source_station and source_station!=station_id):
        raise WatershedDataError('Explicit station conflicts with the SiteSpec or is invalid')
    root = Path(output).resolve()
    geometry_bytes = Path(basin_path).read_bytes()
    frame = gpd.read_file(basin_path).to_crs(4326)
    basin = unary_union(frame.geometry)
    if basin.geom_type not in {'Polygon', 'MultiPolygon'} or basin.is_empty or not basin.is_valid:
        raise WatershedDataError('Valid polygon basin required')
    project = Transformer.from_crs(4326,frame.estimate_utm_crs(),always_xy=True).transform
    area = transform(project, basin).area
    distance = transform(project, basin).distance(transform(project,Point(spec.longitude,spec.latitude)))
    if spec.catchment_area_m2 is None or abs(spec.catchment_area_m2/area-1) > .001 or distance > 100:
        raise WatershedDataError('SiteSpec area/outlet disagrees with supplied basin')
    identity = {'schema':'GaugeBasinForcing-v1','site_spec_sha256':hashlib.sha256(Path(site_spec).read_bytes()).hexdigest(),
                'basin_sha256':hashlib.sha256(geometry_bytes).hexdigest(), 'site_id':spec.site_id,
                'catchment_area_m2':spec.catchment_area_m2,'catchment_area_source':spec.catchment_area_source,
                'start_utc':start.isoformat(),'end_exclusive_utc':end.isoformat()}
    if station_id and not source_station:identity['station_id']=station_id
    if root.exists():
        if not resume or json.loads((root/'request.json').read_text()) != identity:
            raise WatershedDataError('Existing output requires explicit resume with identical inputs')
    else:
        root.mkdir(parents=True)
        (root/'request.json').write_bytes(canonical_json(identity))
        (root/'source_basin').write_bytes(geometry_bytes)
        (root/'basin.geojson').write_bytes(canonical_json({'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':mapping(basin)}]}))
        (root/'site_spec.yaml').write_bytes(Path(site_spec).read_bytes())
    if (root/'manifest.json').exists():
        completed=json.loads((root/'manifest.json').read_text())
        for name, expected in completed['files'].items():
            if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
                raise WatershedDataError('Completed package changed; refusing regeneration')
        for key, entry in json.loads((root/'raw_sources.json').read_text()).items():
            if not re.fullmatch(r'[0-9a-f]{64}',key) or hashlib.sha256((root/'raw'/key).read_bytes()).hexdigest()!=entry['sha256']:
                raise WatershedDataError('Completed raw provenance changed; refusing reuse')
        return root/'manifest.json'
    cache = RawCache(root,max_download_mb,progress)
    rain = {}
    weights_records = {}
    for year in range(start.year,end.year+1):
        base = f'{AORC}/{year}.zarr'
        metadata = json.loads(cache.get(base+'/.zmetadata'))['metadata']
        lats = zarr_axis(cache,base,metadata,'latitude')
        lons = zarr_axis(cache,base,metadata,'longitude')
        times = zarr_axis(cache,base,metadata,'time')
        if metadata['time/.zattrs'].get('units') != 'seconds since 1970-01-01' or not np.all(np.diff(times)==3600):
            raise WatershedDataError('AORC hourly time identities are not understood')
        attrs = metadata['APCP_surface/.zattrs']
        dims = metadata['APCP_surface/.zarray']['chunks']
        if attrs.get('units') != 'kg/m^2' or attrs.get('_ARRAY_DIMENSIONS') != ['time','latitude','longitude']:
            raise WatershedDataError('AORC precipitation dimensions/units changed')
        cells = grid_weights(basin,lats,lons)
        weights_records[f'aorc_{year}'] = [{'latitude':float(lats[i]),'longitude':float(lons[j]),'area_m2':w} for i,j,w in cells]
        for block in range(math.ceil(len(times)/dims[0])):
            ts = times[block*dims[0]:(block+1)*dims[0]]
            # Native AORC accumulation ends at timestamp, unlike interval-start CSV identities.
            eligible = (ts > start.timestamp()) & (ts <= end.timestamp())
            if not eligible.any():continue
            blocks = {(i//dims[1],j//dims[2]) for i,j,_ in cells}
            data = {ij:zarr_chunk(cache,base,metadata,'APCP_surface',[block,*ij]) for ij in blocks}
            for k in np.flatnonzero(eligible):
                values=[]
                for i,j,_ in cells:
                    value=data[(i//dims[1],j//dims[2])][k,i%dims[1],j%dims[2]]
                    values.append(float('nan') if value==attrs['_FillValue'] else float(value)*attrs.get('scale_factor',1)+attrs.get('add_offset',0))
                value,valid,coverage=weighted_value(values,[w for _,_,w in cells])
                epoch=int(ts[k])-3600
                if epoch in rain:raise WatershedDataError('Duplicate AORC interval')
                rain[epoch]=(value,valid,coverage)
            if block%10==0:progress(f'AORC {year}: block {block+1}/{math.ceil(len(times)/dims[0])}; raw sources={len(cache.entries)}',flush=True)
    # Missing provider values remain explicit; never synthesize rainfall or shift time.
    with (root/'rainfall.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['interval_start_epoch_seconds','rain_mm','valid','spatial_coverage'])
        for epoch in range(int(start.timestamp()),int(end.timestamp()),3600):
            value,valid,coverage=rain.get(epoch,(0.,False,0.))
            writer.writerow([epoch,value,int(valid),coverage])
    xmin,ymin,xmax,ymax=basin.bounds
    daily=[]
    for year in range(start.year,(end-timedelta(seconds=1)).year+1):
        params={'var':ET_VARIABLE,'north':ymax+.05,'south':ymin-.05,'west':xmin-.05,'east':xmax+.05,
                'horizStride':1,'time_start':f'{year}-01-01T00:00:00Z','time_end':f'{year}-12-31T00:00:00Z','timeStride':1,'accept':'netcdf'}
        raw=cache.get(GRIDMET+'?'+urllib.parse.urlencode(params))
        with Dataset('gridmet',memory=raw) as ds:
            cells=grid_weights(basin,ds['lat'][:],ds['lon'][:])
            weights_records[f'gridmet_{year}']=[{'latitude':float(ds['lat'][i]),'longitude':float(ds['lon'][j]),'area_m2':w} for i,j,w in cells]
            variable=ds[ET_VARIABLE]
            if variable.dimensions!=('day','lat','lon') or variable.units!='mm':
                raise WatershedDataError('gridMET dimensions/units changed')
            dates=num2date(ds['day'][:],ds['day'].units,only_use_cftime_datetimes=False)
            values=np.ma.filled(variable[:],np.nan)
            for k,date in enumerate(dates):
                day=datetime(date.year,date.month,date.day,7,tzinfo=timezone.utc)
                if day<start or day+timedelta(days=1)>end:continue
                value,valid,coverage=weighted_value([values[k,i,j] for i,j,_ in cells],[w for _,_,w in cells])
                daily.append((int(day.timestamp()),value,int(valid),coverage))
        progress(f'gridMET {year}: reference ET acquired',flush=True)
    with (root/'reference_et.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['day_start_epoch_seconds','reference_et_mm_day','valid','spatial_coverage']);writer.writerows(daily)
    (root/'spatial_weights.json').write_bytes(canonical_json(weights_records))
    files=['rainfall.csv','reference_et.csv','spatial_weights.json','basin.geojson','site_spec.yaml','request.json','raw_sources.json']
    if (root/'source_basin').exists():files.append('source_basin')
    manifest=dict(identity,et_day_start_hour_utc=7,et_semantics='ASCE short-grass reference evapotranspiration',
                  rainfall_semantics='AORC hour-ending accumulation mapped to interval start',
                  missing_rainfall_intervals=sum(not rain.get(t,(0,False,0))[1] for t in range(int(start.timestamp()),int(end.timestamp()),3600)),
                  provider_sources={'rainfall':AORC,'reference_et':GRIDMET},
                  raw_bytes=sum(e['bytes'] for e in cache.entries.values()),outlet_distance_to_basin_m=distance,
                  files={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files},
                  research_training_authorized=False,
                  limitations=['Retrospective weather analyses, not an operational forecast product.',
                               'Urban subsurface drainage is not verified by the topographic basin.',
                               'Strict complete-cell support; unavailable values stay missing.'])
    (root/'manifest.json').write_bytes(canonical_json(manifest))
    return root/'manifest.json'
