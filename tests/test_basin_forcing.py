import io
import json
import pytest
from shapely.geometry import box
from ohqbuilder.watershed_data.basin_forcing import grid_weights, weighted_value, RawCache
from ohqbuilder.watershed_data.schemas import WatershedDataError


def test_weights_cover_polygon_and_respect_reversed_latitude():
    a=grid_weights(box(-77.04,38.96,-76.96,39.04),[38.95,39.05],[-77.05,-76.95])
    b=grid_weights(box(-77.04,38.96,-76.96,39.04),[39.05,38.95],[-77.05,-76.95])
    assert len(a)==len(b)==4
    assert sum(x[2] for x in a)==pytest.approx(sum(x[2] for x in b))
    assert weighted_value([1,3],[1,3])==(2.5,True,1.)


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-1])
def test_missing_cell_is_not_renormalized_or_zero_rain(bad):
    mean,valid,coverage=weighted_value([0,bad],[1,1])
    assert not valid and mean==0 and coverage==.5


def test_provider_grid_cannot_cover_only_part_of_basin():
    with pytest.raises(WatershedDataError,match='entire basin'):
        grid_weights(box(-78,38,-76,40),[38.95,39.05],[-77.05,-76.95])


def test_irregular_or_nonfinite_grid_rejected():
    for lats in [[39,38.8,38.9],[38.9,float('nan')]]:
        with pytest.raises(WatershedDataError):grid_weights(box(-77.04,38.96,-76.96,39.04),lats,[-77.05,-76.95])


def test_cache_is_hashed_and_reused_without_network(tmp_path,monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(b'example'))
    cache=RawCache(tmp_path,1,print)
    assert cache.get('https://example.test')==b'example'
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**kw:pytest.fail('unexpected network'))
    assert RawCache(tmp_path,1,print).get('https://example.test')==b'example'
    entry=next(iter(cache.entries))
    (tmp_path/'raw'/entry).write_bytes(b'corrupt')
    with pytest.raises(WatershedDataError,match='hash mismatch'):cache.get('https://example.test')


def test_download_budget_is_enforced(tmp_path,monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(b'0123456789'))
    with pytest.raises(WatershedDataError,match='byte budget'):
        RawCache(tmp_path,5/1024**2,print).get('https://example.test')
    assert not (tmp_path/'raw_sources.json').exists()


def test_cli_dispatch_uses_real_producer_api(monkeypatch,tmp_path):
    import ohqbuilder.watershed_data.basin_forcing as module
    from ohqbuilder.cli import main
    seen={}
    def fake(site_spec,basin,output,**kwargs):
        seen.update(site_spec=site_spec,basin=basin,output=output,**kwargs)
        return tmp_path/'manifest.json'
    monkeypatch.setattr(module,'acquire_basin_forcing',fake)
    assert main(['data','basin-forcing','--site-spec','site.yaml','--basin','basin.geojson',
                 '--output',str(tmp_path/'new'),'--max-download-mb','12','--resume'])==0
    assert seen['resume'] is True and seen['max_download_mb']==12
    assert seen['basin']=='basin.geojson'


@pytest.mark.parametrize('input_format',['geojson','projected_gpkg'])
def test_real_cli_producer_path_with_fabricated_zarr_and_netcdf(tmp_path,monkeypatch,input_format):
    """Exercise download/decode/weights/export/resume without real network."""
    import datetime
    import subprocess
    import urllib.request
    import numpy as np
    import yaml
    import geopandas as gpd
    from netCDF4 import Dataset
    from ohqbuilder.watershed_data.basin_forcing import acquire_basin_forcing, AORC, GRIDMET, ET_VARIABLE
    basin=box(-77.005,38.995,-76.995,39.005)
    frame=gpd.GeoDataFrame(geometry=[basin],crs=4326)
    polygon=tmp_path/('basin.geojson' if input_format=='geojson' else 'basin.gpkg')
    (frame if input_format=='geojson' else frame.to_crs(26918)).to_file(polygon,driver='GeoJSON' if input_format=='geojson' else 'GPKG')
    area=frame.to_crs(frame.estimate_utm_crs()).geometry.area.iloc[0]
    site=tmp_path/'site.yaml'
    site.write_text(yaml.safe_dump({'site_id':'fabricated','geometry':{'outlet':{'longitude':-77.,'latitude':39.}},
        'study_period':{'start':'2015-01-01T00:00:00Z','end':'2015-01-13T11:00:00Z'},
        'catchment':{'area_m2':float(area),'source':'synthetic geometry'}}))
    meta={};responses={};base=AORC+'/2015.zarr'
    def add(name,values,chunks,attrs):
        values=np.asarray(values)
        meta[name+'/.zarray']={'zarr_format':2,'filters':None,'order':'C','compressor':{'id':'zstd'},
                              'chunks':chunks,'shape':list(values.shape),'dtype':values.dtype.str}
        meta[name+'/.zattrs']=attrs
        if len(chunks)==1:
            for i in range(int(np.ceil(len(values)/chunks[0]))):
                data=np.zeros(chunks,dtype=values.dtype);part=values[i*chunks[0]:(i+1)*chunks[0]];data[:len(part)]=part
                responses[base+'/'+name+'/'+str(i)]=subprocess.check_output(['zstd','-q','-c'],input=data.tobytes())
        else:
            for i in range(int(np.ceil(values.shape[0]/chunks[0]))):
                data=np.zeros(chunks,dtype=values.dtype);part=values[i*chunks[0]:(i+1)*chunks[0]];data[:len(part)]=part
                responses[base+'/'+name+'/'+str(i)+'.0.0']=subprocess.check_output(['zstd','-q','-c'],input=data.tobytes())
    epoch=1420070400
    add('latitude',np.array([38.99,39.01],dtype='<f8'),[2],{})
    add('longitude',np.array([-77.01,-76.99],dtype='<f8'),[2],{})
    add('time',np.arange(301,dtype='<i8')*3600+epoch,[144],{'units':'seconds since 1970-01-01'})
    add('APCP_surface',np.full((301,2,2),10,dtype='<i2'),[144,2,2],
        {'units':'kg/m^2','_ARRAY_DIMENSIONS':['time','latitude','longitude'],'_FillValue':-32767,'scale_factor':.1})
    responses[base+'/.zmetadata']=json.dumps({'metadata':meta}).encode()
    nc=tmp_path/'gridmet.nc'
    with Dataset(nc,'w') as ds:
        for name,values in [('lat',[38.99,39.01]),('lon',[-77.01,-76.99]),('day',list(range(42003,42368)))]:
            ds.createDimension(name,len(values));v=ds.createVariable(name,'f8',(name,));v[:]=values
            if name=='day':v.units='days since 1900-01-01 00:00:00'
        v=ds.createVariable(ET_VARIABLE,'f4',('day','lat','lon'));v.units='mm';v[:]=2.4
    ncbytes=nc.read_bytes()
    def opener(request,**kwargs):
        url=request.full_url
        return io.BytesIO(ncbytes if url.startswith(GRIDMET+'?') else responses[url])
    monkeypatch.setattr(urllib.request,'urlopen',opener)
    output=tmp_path/'export'
    result=acquire_basin_forcing(site,polygon,output,progress=lambda *a,**k:None)
    manifest=json.loads(result.read_text())
    assert manifest['missing_rainfall_intervals']==0 and manifest['et_day_start_hour_utc']==7
    assert (output/'source_basin').read_bytes()==polygon.read_bytes()
    canonical=json.loads((output/'basin.geojson').read_text())
    assert box(-77.01,38.99,-76.99,39.01).covers(__import__('shapely.geometry',fromlist=['shape']).shape(canonical['features'][0]['geometry']))
    import csv
    rows=list(csv.DictReader((output/'rainfall.csv').open()))
    assert len(rows)==300 and rows[0]['rain_mm']=='1.0'
    assert rows[0]['interval_start_epoch_seconds']==str(epoch)
    days=list(csv.DictReader((output/'reference_et.csv').open()))
    assert len(days)==12 and int(days[0]['day_start_epoch_seconds'])==epoch+7*3600
    original=result.read_bytes()
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**k:pytest.fail('resume redownloaded'))
    assert acquire_basin_forcing(site,polygon,output,resume=True)==result
    assert result.read_bytes()==original
    with pytest.raises(WatershedDataError,match='Existing output'):
        acquire_basin_forcing(site,polygon,output)
    (output/'rainfall.csv').write_text('corrupted')
    with pytest.raises(WatershedDataError,match='Completed package changed'):
        acquire_basin_forcing(site,polygon,output,resume=True)


def test_launcher_plugin_share_basin_command_and_require_explicit_gauge():
    from ohqbuilder.ui.launcher import watershed_data_command,LauncherError
    from qgis_plugin.gistoohq_dem_workflow.dock import _command_for_watershed_data,QgisDockConfigError
    options=dict(site_spec='gauge.yaml',station_id='01650800',basin_polygon='gauge.geojson',
                 basin_weather_output='new weather folder',weather_download_limit_mb=512)
    for action in ['basin-forcing','basin-forcing-resume']:
        first=watershed_data_command(action,**options)
        second=_command_for_watershed_data(action,**options)
        assert list(first.argv)==second
        assert ('--resume' in second)==action.endswith('-resume')
        assert '--station-id' in second and second[second.index('--station-id')+1]=='01650800'
    options['station_id']=''
    with pytest.raises(LauncherError):watershed_data_command('basin-forcing',**options)
    with pytest.raises(QgisDockConfigError):_command_for_watershed_data('basin-forcing',**options)
