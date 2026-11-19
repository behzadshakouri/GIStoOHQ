#!/usr/bin/env python3
"""Synthetic shared-rain-source area scaling and catchment mass-ledger checks."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ohqbuilder.model.watershed import Watershed
from ohqbuilder.writers.rainfall_writer import rainfall_lines


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read_columns(path):
    rows=list(csv.reader(Path(path).open()))
    if len(rows)<3 or len(rows[0])%2:raise ValueError('Incomplete solver table')
    result={}
    for col in range(0,len(rows[0]),2):
        if rows[0][col].strip()!='t':raise ValueError('Expected paired time/value columns')
        name=rows[0][col+1].strip()
        if name in result:raise ValueError('Duplicate output quantity')
        values=[(float(row[col]),float(row[col+1])) for row in rows[1:]]
        if any(not math.isfinite(t) or not math.isfinite(v) for t,v in values):raise ValueError('Nonfinite solver table')
        if abs(values[0][0])>1e-9 or abs(values[-1][0]-.1)>1e-9 or any(b[0]<=a[0] for a,b in zip(values,values[1:])):
            raise ValueError('Incomplete/unordered synthetic support')
        result[name]=values
    return result


def integral(series):
    return sum((b[0]-a[0])*(a[1]+b[1])/2 for a,b in zip(series,series[1:]))


def check(columns,areas,runoff,loss):
    if not columns or any(not math.isfinite(t) or not math.isfinite(v) for series in columns.values() for t,v in series):
        raise ValueError('Nonfinite/empty water-ledger input')
    duration=.1;rain_rate=.01;expected_rain=rain_rate*duration*sum(areas)
    initial=final=loss_volume=discarded=0.
    per_area={}
    for label,area,q in zip(('A','B'),areas,(.1,.2)):
        precipitation=columns[label+'_Precipitation']
        error=max(abs(value-rain_rate*area) for _,value in precipitation)
        if error>1e-8:raise ValueError('Rainfall does not scale with receiving area: '+label)
        storage=columns[label+'_Storage']
        s0=area*.01
        if abs(storage[0][1]-s0)>1e-6:raise ValueError('Unexpected initial storage')
        net=rain_rate*area*runoff-q
        expected=(s0*math.exp(-loss*duration)+net/loss*(-math.expm1(-loss*duration))) if loss else s0+net*duration
        if abs(storage[-1][1]-expected)>1e-3:raise ValueError('Trajectory disagrees with independent analytic solution')
        initial+=storage[0][1];final+=storage[-1][1]
        loss_volume-=integral(columns[label+'_loss'])
        discarded-=integral(columns[label+'_Precipitation_loss'])
        per_area[label]={'area_m2':area,'rain_m3_day':rain_rate*area,'storage_final_m3':storage[-1][1],
                         'analytic_storage_final_m3':expected}
    outlet=columns['Collector_Storage'][-1][1]-columns['Collector_Storage'][0][1]
    if abs(outlet-.3*duration)>1e-6:raise ValueError('Outlet collector disagrees with fixed discharge')
    residual=expected_rain-(final-initial)-outlet-loss_volume-discarded
    if abs(residual)>1e-3:raise ValueError('Water ledger does not close: '+str(residual))
    return {'expected_rain_m3':expected_rain,'storage_change_m3':final-initial,'outlet_m3':outlet,
            'first_order_loss_m3':loss_volume,'nonrunoff_rain_loss_m3':discarded,'mass_residual_m3':residual,'receivers':per_area}


def run(binary,resources,output):
    binary,resources,output=map(lambda p:Path(p).resolve(),(binary,resources,output))
    output.mkdir(parents=True,exist_ok=False)
    receipt={'binary_sha256':sha(binary),'script_sha256':sha(__file__),
             'rainfall_writer_sha256':sha(Path(__file__).resolve().parents[1]/'ohqbuilder/writers/rainfall_writer.py'),
             'resource_sha256':{n:sha(resources/n) for n in ['main_components.json','mass_transfer.json','settings.json']},
             'cases':[],'synthetic_only':True,'research_fits':0,
             'limitations':'Two simple catchments and a collector, fixed outflow, one constant rainfall interval. Not field-event or full-HRU water-balance validation.'}
    def limits():
        resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
    for name,areas,runoff,loss in [('conservative',(1000.,2500.),1.,0.),('losses',(1000.,2500.),.6,.2),('swapped_receivers',(2500.,1000.),1.,0.)]:
        folder=output/name;folder.mkdir();rain=folder/'rain.txt'
        # Native precipitation triples: start day, end day, interval TOTAL depth m.
        # Cover a full day so the synthetic run never samples an endpoint cutoff.
        rain.write_text('0,1,0.01\n')
        old=os.environ.get('OHQ_RAINFALL_FILE');os.environ['OHQ_RAINFALL_FILE']=str(rain)
        try:lines=rainfall_lines(Watershed('Synthetic rainfall ledger'))
        finally:
            if old is None:os.environ.pop('OHQ_RAINFALL_FILE',None)
            else:os.environ['OHQ_RAINFALL_FILE']=old
        prefix=[f'loadtemplate; filename={resources/"main_components.json"}',f'addtemplate; filename={resources/"mass_transfer.json"}']
        for label,area in zip(('A','B'),areas):
            lines.append(f'create block;type=Catchment,name={label},area={area},depth=0.01,ManningCoeff=0.035,Precipitation=Rain,Runoff_coeff={runoff},loss_coefficient={loss},elevation=0')
        lines.append('create block;type=Catchment,name=Collector,area=1,depth=0,ManningCoeff=0.035,elevation=0')
        for label,q in [('A',.1),('B',.2)]:lines.append(f'create link;type=Fixed flow,name={label}_out,from={label},to=Collector,flow={q}')
        settings={'simulation_start_time':0,'simulation_end_time':.1,'initial_time_step':.001,'max_timestep_increase_factor':1,'c_n_weight':.5,'nr_tolerance':1e-9,'alloutputfile':'output.csv'}
        lines += [f'setvalue;object=system,quantity={k},value={v}' for k,v in settings.items()]
        model=folder/'model.ohq';model.write_text('\n'.join(prefix+lines)+'\n')
        command=[str(binary),str(model),'--resources',str(resources),'--workdir',str(folder)]
        with (folder/'solver.log').open('x') as log:
            result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=60,preexec_fn=limits,env=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2'))
        if result.returncode:raise RuntimeError(name+' solver failed; inspect saved log')
        ledger=check(read_columns(folder/'output.csv'),areas,runoff,loss)
        ledger.update(name=name,command=command,model_sha256=sha(model),rain_sha256=sha(rain),output_sha256=sha(folder/'output.csv'))
        receipt['cases'].append(ledger)
        (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['binary','resources','new_output']:p.add_argument(name,type=Path)
    a=p.parse_args();run(a.binary,a.resources,a.new_output)
