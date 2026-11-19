#!/usr/bin/env python3
"""Isolated T4 topology, overtopping and accepted-step water-balance regression."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from verify_ohq_forcing_compatibility import native, sha


def run(binary, resources, model, output, block_tolerance=None):
    binary,resources,model,output=(Path(p).resolve() for p in (binary,resources,model,output))
    output.mkdir(exist_ok=False,parents=True)
    text=model.read_text()
    if re.search(r'create\s+(observation|objectivefunction|parameter)\s*;',text,re.I):
        raise ValueError('Forward-only fixture required')
    links={}
    for line in text.splitlines():
        if line.startswith('create link;'):
            fields=dict(token.strip().split('=',1) for token in line.split(';',1)[1].split(','))
            links[fields['name']]=(fields['from'],fields['to'])
    required={'Inlet':('US_Channel','Barrel'),'Outlet':('Barrel','DS_Channel'),
              'Tailwater':('DS_Channel','Outfall'),'Road':('US_Channel','DS_Channel')}
    if links!=required:raise ValueError('T4 missing/incorrect hydraulic connection')
    # The native diagnostic main saves every requested balance term at 17 digits.
    # Record every accepted step; do not change any solver/physical settings.
    for filename in set(re.findall(r'=\s*([^,\n;]+\.csv)(?=,|\n|$)',text)):
        p=(model.parent/filename).resolve()
        if p.parent!=model.parent:raise ValueError('Nonlocal fixture forcing')
        shutil.copy2(p,output/p.name)
    text+='\nsetvalue;object=system,quantity=write_interval,value=1\n'
    if block_tolerance is not None:
        if not math.isfinite(block_tolerance) or block_tolerance<=0:raise ValueError('Invalid numerical tolerance')
        text+=f'setvalue;object=system,quantity=nr_block_tolerance,value={block_tolerance}\n'
    (output/'model.ohq').write_text(text)
    start=time.monotonic()
    with (output/'solver.log').open('w') as log:
        result=subprocess.run([str(binary),str(output/'model.ohq'),str(output),str(output/'native.csv')],stdout=log,stderr=subprocess.STDOUT,timeout=60,
            env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
    if result.returncode:raise ValueError(f'Failed solve: {result.returncode}')
    data=native(output/'native.csv')
    states=[name+'_Storage' for name in ('US_Channel','Barrel','DS_Channel')]
    times=[t for t,v in data[states[0]]]
    if len(times)<3 or times[0]!=0 or abs(times[-1]-2)>1e-10:raise ValueError('Incomplete T4 support')
    for name,series in data.items():
        if [t for t,v in series] not in (times,times[1:]):raise ValueError('Mismatched accepted-step support')
    inventory=[line.split('\t') for line in (output/'inventory.tsv').read_text().splitlines()]
    external=[row[1]+'_'+row[2] for row in inventory if row[0]=='external' and row[1]!='Outfall']
    boundary=[row for row in inventory if row[0]=='link' and 'Outfall' in row[2:]]
    if boundary!=[['link','Tailwater','DS_Channel','Outfall']]:raise ValueError('Unexpected boundary topology')
    # Link rates begin at the first accepted step end, while states include t0.
    integrate=lambda series:series[0][0]*series[0][1]+math.fsum((b[0]-a[0])*b[1] for a,b in zip(series,series[1:]))
    terms={key:integrate(data[key]) for key in external}
    volume_in=math.fsum(terms.values());volume_out=integrate(data['Tailwater_flow'])
    storage_change=math.fsum(data[key][-1][1]-data[key][0][1] for key in states)
    residual=volume_in-volume_out-storage_change
    tolerance=.01+1e-6*max(abs(volume_in),abs(volume_out))
    minimum_storage=min(v for key in states for t,v in data[key])
    road=integrate(data['Road_flow']);outlet=integrate(data['Outlet_flow'])
    receipt=dict(binary_sha256=sha(binary),original_model_sha256=sha(model),model_sha256=sha(output/'model.ohq'),
        numerical_block_tolerance_override=block_tolerance,wall_seconds=time.monotonic()-start,accepted_rows=len(times),external_volumes_m3=terms,
        boundary_outflow_m3=volume_out,storage_change_m3=storage_change,residual_m3=residual,tolerance_m3=tolerance,
        minimum_storage_m3=minimum_storage,road_volume_m3=road,barrel_outlet_volume_m3=outlet,
        whole_domain_balance_pass=abs(residual)<=tolerance,nonnegative_storage_pass=minimum_storage>=-1e-6,
        overtopping_and_barrel_flow_pass=road>0 and outlet>0)
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))
    return all(receipt[key] for key in ('whole_domain_balance_pass','nonnegative_storage_pass','overtopping_and_barrel_flow_pass'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('binary','resources','model','new_output'):p.add_argument(name,type=Path)
    p.add_argument('--block-tolerance',type=float)
    a=p.parse_args();raise SystemExit(0 if run(a.binary,a.resources,a.model,a.new_output,a.block_tolerance) else 1)
