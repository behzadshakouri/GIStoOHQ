#!/usr/bin/env python3
"""Read-only Sligo gauge/upstream-basin check on existing GIStoOHQ flow grids.

No downloads, observations, DEM regeneration or original-file writes. The
upstream traversal uses the production fallback's actual GRASS direction map.
Outputs are review candidates, not an accepted replacement input package.
"""
import argparse
import ast
from collections import deque
import csv
import hashlib
import io
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape, mapping
from shapely.ops import unary_union, transform
import yaml


def trace(flow, start, offsets):
    mask = np.zeros(flow.shape, dtype=bool)
    mask[start] = True
    queue = deque([start])
    while queue:
        r, c = queue.popleft()
        for code, (dr, dc) in offsets.items():
            rr, cc = r-dr, c-dc
            if 0 <= rr < flow.shape[0] and 0 <= cc < flow.shape[1] and not mask[rr, cc] and abs(float(flow[rr, cc])) == code:
                mask[rr, cc] = True
                queue.append((rr, cc))
    return mask


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('outputs', type=Path)
    p.add_argument('site_spec', type=Path)
    p.add_argument('station_metadata_rdb', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--plot', action='store_true', help='Also save a local comparison figure')
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    spec = yaml.safe_load(a.site_spec.read_text())
    lines = [line for line in a.station_metadata_rdb.read_text().splitlines() if line and not line.startswith('#')]
    records = list(csv.DictReader(io.StringIO('\n'.join([lines[0]]+lines[2:])), delimiter='\t'))
    station = [r for r in records if r['site_no']=='01650800']
    if len(station)!=1: raise ValueError('Exactly one USGS 01650800 metadata record required')
    station = station[0]
    paths = [a.outputs/'flow_dir.tif', a.outputs/'flow_acc.tif', a.outputs/'watershed_boundary.gpkg',
             a.site_spec, a.station_metadata_rdb]
    script = Path(__file__).resolve().parent/'legacy_gis/delineate_whole_watershed.py'
    paths.extend([script, Path(__file__).resolve()])
    tree = ast.parse(script.read_text())
    function = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='delineate_watershed_with_flowdir')
    assignment = next(n for n in ast.walk(function) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='direction_offsets' for t in n.targets))
    offsets = ast.literal_eval(assignment.value)
    with rasterio.open(paths[0]) as ds:
        flow, aff, crs = ds.read(1), ds.transform, ds.crs
    with rasterio.open(paths[1]) as ds:
        if (ds.shape, ds.transform, ds.crs)!=(flow.shape, aff, crs): raise ValueError('Flow grids do not match')
        acc = ds.read(1)
    if not crs.is_projected or crs.linear_units!='metre': raise ValueError('Projected metre grid required')
    project = Transformer.from_crs(4326,crs,always_xy=True).transform
    unproject = Transformer.from_crs(crs,4326,always_xy=True).transform
    results, masks = {}, {}
    for name, lon, lat in [('gauge',float(station['dec_long_va']),float(station['dec_lat_va'])),
                           ('reviewed_outlet',spec['geometry']['outlet']['longitude'],spec['geometry']['outlet']['latitude'])]:
        x,y = project(lon,lat); r,c = rasterio.transform.rowcol(aff,x,y)
        if not (0<=r<flow.shape[0] and 0<=c<flow.shape[1]): raise ValueError('Point outside grids')
        candidates=[]
        radius=int(np.ceil(50/min(abs(aff.a),abs(aff.e))))+1
        for rr in range(max(0,r-radius),min(flow.shape[0],r+radius+1)):
            for cc in range(max(0,c-radius),min(flow.shape[1],c+radius+1)):
                xx,yy=aff*(cc+.5,rr+.5); distance=float(np.hypot(xx-x,yy-y))
                if distance<=50 and np.isfinite(acc[rr,cc]) and abs(acc[rr,cc])>=50:
                    candidates.append((float(abs(acc[rr,cc])),-distance,rr,cc))
        if not candidates: raise ValueError('No routed cell inside fixed50m limit; no wider search')
        accumulation,negdistance,r,c=max(candidates)
        mask=trace(flow,(r,c),offsets);masks[name]=mask
        polygon=unary_union([shape(g) for g,value in shapes(mask.astype('uint8'),mask=mask,transform=aff) if value==1])
        result={'input_longitude':lon,'input_latitude':lat,'snap_distance_m':-negdistance,
                'snapped_cell': [r,c], 'cells':int(mask.sum()),'flow_accumulation_cells':accumulation,
                'area_km2':float(mask.sum()*abs(aff.a*aff.e-aff.b*aff.d)/1e6)}
        results[name]=result
        (a.output/(name+'_candidate.geojson')).write_text(json.dumps({'type':'FeatureCollection','features':[
            {'type':'Feature','properties':dict(result,review_status='candidate_only'),
             'geometry':mapping(transform(unproject,polygon))}]})+'\n')
        if name=='reviewed_outlet':
            existing=gpd.read_file(paths[2]).to_crs(crs).geometry.union_all()
            result['saved_boundary_area_km2']=existing.area/1e6
            result['saved_boundary_iou']=existing.intersection(polygon).area/existing.union(polygon).area
    results['gauge']['usgs_area_km2']=float(station['drain_area_va'])*2.589988110336
    results['gauge']['area_difference_percent']=100*(results['gauge']['area_km2']/results['gauge']['usgs_area_km2']-1)
    report={'results':results,'gauge_is_routed_upstream_subset':not bool(np.any(masks['gauge'] & ~masks['reviewed_outlet'])),
            'source_sha256':{str(path.resolve()):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
            'direction_offsets':offsets,'snap_rule':'Maximum absolute accumulation within fixed50m; no area tuning',
            'new_training_ready':False,
            'limitations':'Candidate on existing conditioned DEM. Basin/forcing spatial review still required; no observations read.'}
    (a.output/'audit.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    if a.plot:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch
        fig, ax = plt.subplots(figsize=(7, 7))
        for name, color in [('reviewed_outlet', '#dce5ec'), ('gauge', '#4d91ab')]:
            gpd.read_file(a.output/(name+'_candidate.geojson')).to_crs(crs).plot(
                ax=ax, color=color, edgecolor='#24475a', linewidth=.9)
        for name, color, marker in [('gauge', '#112d40', 'o'), ('reviewed_outlet', '#c24735', 'v')]:
            item=results[name];x,y=project(item['input_longitude'],item['input_latitude'])
            ax.scatter(x,y,s=65,color=color,marker=marker,zorder=5)
            label='USGS 01650800' if name=='gauge' else 'Reviewed model outlet'
            ax.annotate(label,(x,y),xytext=((8,12) if name=='gauge' else (-10,-18)),
                        ha=('left' if name=='gauge' else 'right'),textcoords='offset points',fontsize=9)
        fig.legend(handles=[Patch(facecolor='#4d91ab',label='Gauge-upstream candidate: 16.751 km²'),
                           Patch(facecolor='#dce5ec',label='Downstream model basin: 23.665 km²'),
                           Line2D([],[],color='none',label='USGS listed drainage area: 16.705 km²')],
                  loc='lower center',bbox_to_anchor=(.5,.055),frameon=False,fontsize=9)
        ax.set_title('Sligo Creek catchments\nGauge basin and reviewed downstream outlet',loc='left',pad=16,fontsize=13)
        ax.set_xlabel('Easting (m), NAD83 / UTM18N');ax.set_ylabel('Northing (m)')
        ax.ticklabel_format(useOffset=False,style='plain');ax.set_aspect('equal')
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(4))
        ax.spines[['top','right']].set_visible(False)
        fig.text(.12,.02,'Existing flow grids; fixed 50 m snap limit. Candidate only; no discharge observations read.',fontsize=8)
        fig.tight_layout(rect=[0,.20,1,1]);fig.savefig(a.output/'catchment_comparison.png',dpi=180);plt.close(fig)
    print(json.dumps(report['results'],indent=2))


if __name__=='__main__': main()
