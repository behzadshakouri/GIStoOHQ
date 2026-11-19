#!/usr/bin/env python3
"""Verify a shared rainfall source evaluates coefficients per receiver."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from verify_ohq_forcing_compatibility import native, sha


def run(binary,resources,output):
    binary,resources,output=(Path(p).resolve() for p in (binary,resources,output))
    output.mkdir(parents=True,exist_ok=False)
    receipt={'binary_sha256':sha(binary),'cases':[]}
    for uniform in ('Yes','No'):
        for dt in (.007,.06):
            for reverse in (False,True):
                folder=output/f'{uniform}_{dt}_{reverse}';folder.mkdir()
                bins=[(0.,.02,.001),(.03,.05,.002),(.05,.1,0.)]
                rain=folder/'rain.csv';rain.write_text(''.join(f'{a},{b},{d}\n' for a,b,d in bins))
                receivers=[('Small',2.),('Medium',1000.),('Large',2300.)]
                if reverse:receivers.reverse()
                text=f'loadtemplate;filename={resources}/main_components.json\naddtemplate;filename={resources}/rainfall_runoff.json\ncreate source;type=Precipitation,name=Rain,timeseries={rain}[m]\n'
                for name,area in receivers:
                    text+=f'create block;type=Catchment,name={name},area={area},depth=.01,ManningCoeff=.035,Precipitation=Rain,Runoff_coeff=1,loss_coefficient=0,elevation=0\n'
                for k,v in dict(simulation_start_time=0,simulation_end_time=.1,initial_time_step=dt,make_timeseries_uniform=uniform,max_timestep_increase_factor=1,nr_tolerance=1e-10,nr_block_tolerance=1e-9).items():
                    text+=f'setvalue;object=system,quantity={k},value={v}\n'
                model=folder/'model.ohq';model.write_text(text)
                with (folder/'solver.log').open('w') as log:
                    result=subprocess.run([str(binary),str(model),str(folder),str(folder/'native.csv')],stdout=log,stderr=subprocess.STDOUT,timeout=30,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
                if result.returncode:raise ValueError(f'Failed shared-source solve: {folder}')
                values=native(folder/'native.csv');errors={}
                for name,area in receivers:
                    samples=values[name+'_Storage']
                    if len(samples)<2 or abs(samples[0][0])>1e-10 or abs(samples[-1][0]-.1)>1e-10:raise ValueError('Incomplete support')
                    def exact(t):return .01*area+sum(d*area*max(0.,min(t,b)-a)/(b-a) for a,b,d in bins if t>a)
                    error=max(abs(v-exact(t)) for t,v in samples)
                    if error>1e-7:raise ValueError(f'Receiver coefficient/interval error: {name} {error}')
                    errors[name]=error
                receipt['cases'].append({'uniform':uniform,'dt':dt,'reverse_receiver_order':reverse,'storage_max_error_m3':errors,'model_sha256':sha(model)})
                (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
                print(folder.name,'PASS',flush=True)
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('binary','resources','new_output'):p.add_argument(n,type=Path)
    a=p.parse_args();run(a.binary,a.resources,a.new_output)
