#!/usr/bin/env python3
"""Analytic native-rain forcing matrix: units, calendar offset and stepping."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import struct
from analyze_ohq_generated_event import sha


def native(path):
    result={};name=None
    for line in Path(path).read_text().splitlines():
        if line.startswith('#'):
            name=line[1:]
            if name in result:raise ValueError('Duplicate native quantity')
            result[name]=[]
        else:
            t,v=map(float,line.split(','))
            if not math.isfinite(t) or not math.isfinite(v):raise ValueError('Nonfinite native output')
            if result[name] and t<=result[name][-1][0]:raise ValueError('Unordered native support')
            result[name].append((t,v))
    return result


def run(binary,resources,output):
    binary,resources,output=map(lambda p:Path(p).resolve(),(binary,resources,output))
    output.mkdir(exist_ok=False,parents=True)
    receipt={'binary_sha256':sha(binary),'cases':[],'synthetic_only':True}
    cases=[]
    for uniform in ('Yes','No'):
        for step in (.0007,.007,.05,.2):cases.append((f'{uniform}_{step}','m',1.,0.,step,uniform,1))
    cases += [('adaptive','m',1.,0.,.0007,'Yes',50),('calendar','m',1.,36526.,.007,'Yes',1),
              ('millimetres','mm',.001,0.,.007,'Yes',1),('inches','in',.0254,0.,.007,'Yes',1)]
    for name,unit,factor,offset,dt,uniform,growth in cases:
        folder=output/name;folder.mkdir()
        rain=folder/'rain.csv';bins=[(offset,offset+.02,0.),(offset+.02,offset+.04,.002),(offset+.04,offset+.1,0.)]
        rain.write_text(''.join(f'{a:.17g},{b:.17g},{depth/factor:.17g}\n' for a,b,depth in bins))
        model=f'''loadtemplate;filename={resources}/main_components.json
addtemplate;filename={resources}/rainfall_runoff.json
create source;type=Precipitation,name=Rain,timeseries={rain}[{unit}]
create block;type=Catchment,name=A,area=1000,depth=.01,ManningCoeff=.035,Precipitation=Rain,Runoff_coeff=1,loss_coefficient=0,elevation=0
'''
        settings=dict(simulation_start_time=offset,simulation_end_time=offset+.1,initial_time_step=dt,
            make_timeseries_uniform=uniform,max_timestep_increase_factor=growth,nr_tolerance=1e-10,nr_block_tolerance=1e-9)
        for k,v in settings.items():model+=f'setvalue;object=system,quantity={k},value={v}\n'
        (folder/'model.ohq').write_text(model)
        with (folder/'solver.log').open('w') as log:
            p=subprocess.run([str(binary),str(folder/'model.ohq'),str(folder),str(folder/'native.csv')],stdout=log,stderr=subprocess.STDOUT,timeout=30,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
        if p.returncode:raise ValueError(f'{name} failed to solve: {p.returncode}')
        values=native(folder/'native.csv');s=values['A_Storage'];q=values['A_Precipitation_runoff']
        if abs(s[0][0]-offset)>1e-9 or abs(s[-1][0]-(offset+.1))>1e-9 or len(s)<2:raise ValueError('Incomplete calendar support')
        # XString's established conversion table stores coefficients as float32.
        # Preserve that existing behavior: separate unit quantization from the
        # interval-volume calculation rather than hide it in a looser tolerance.
        stored_factor=struct.unpack('f',struct.pack('f',factor))[0]
        converted_bins=[(a,b,depth/factor*stored_factor) for a,b,depth in bins]
        # Independent interval-overlap solution, not the implementation helper.
        def volume(a,b):return math.fsum(depth*max(0.,min(b,end)-max(a,start))/(end-start)*1000 for start,end,depth in converted_bins)
        storage_error=max(abs(v-(10+volume(offset,t))) for t,v in s)
        rate_error=max(abs(after[1]-volume(before[0],after[0])/(after[0]-before[0])) for before,after in zip(q,q[1:]))
        if storage_error>1e-7 or rate_error>1e-6:raise ValueError(f'{name} violates independent interval solution: storage={storage_error}, rate={rate_error}')
        receipt['cases'].append(dict(name=name,unit=unit,calendar_offset=offset,initial_dt=dt,uniform=uniform,growth=growth,
            storage_max_error_m3=storage_error,rate_max_error_m3_per_day=rate_error,stored_unit_coefficient=stored_factor,physical_unit_quantization_m3=volume(offset,offset+.1)-2.,actual_rows=len(s),model_sha256=sha(folder/'model.ohq')))
        (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(name,'PASS',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('binary','resources','new_output'):p.add_argument(n,type=Path)
    a=p.parse_args();run(a.binary,a.resources,a.new_output)
