#!/usr/bin/env python3
"""Preserve minimal positive/negative channel and HRU validation evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import subprocess


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(console, probe, resources, output, expect_current=False):
    console, probe, resources, output = map(lambda p: Path(p).resolve(), (console,probe,resources,output))
    output.mkdir(parents=True,exist_ok=False)
    prefix=''.join(f'{command}; filename={resources/name}\n' for command,name in [
        ('loadtemplate','main_components.json'),('addtemplate','rainfall_runoff.json'),
        ('addtemplate','open_channel.json'),('addtemplate','hydrologic_response_unit.json')])
    common='setvalue; object=system, quantity=simulation_start_time, value=0\nsetvalue; object=system, quantity=simulation_end_time, value=0.000001\n'
    channel='create block; type=Trapezoidal Channel Segment,name=Channel,base_width=3,side_slope=2,ManningCoeff=0.035,bottom_elevation=0,depth=0.01,length=100\n'
    hru='create source;type=Precipitation,name=Rain\ncreate composite;type=Hydrologic_Response_Unit,name=Catchment,area=10000,catchment_slope=0.01,catchment_width=100,K_sat=0.1,alpha_vG=2,n_vG=1.41,theta_sat=0.45,theta_res=0.067,initial_moisture_content=0.2,Precipitation=Rain,surface_elevation=10\n'
    fixtures={'channel_valid':channel,'channel_invalid_length':channel.replace('length=100','length=-100'),
              'hru_valid':hru,'hru_invalid_moisture':hru.replace('initial_moisture_content=0.2','initial_moisture_content=0.9')}
    receipt={'console_sha256':sha(console),'probe_sha256':sha(probe),'resources':{name:sha(resources/name) for name in ['main_components.json','rainfall_runoff.json','open_channel.json','hydrologic_response_unit.json','settings.json']},'runs':[],'synthetic_only':True}
    def limits():
        resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
    for name,body in fixtures.items():
        folder=output/name;folder.mkdir();model=folder/'model.ohq';model.write_text(prefix+body+common)
        for kind,command in [('console',[str(console),str(model),'--resources',str(resources),'--workdir',str(folder),'--quiet']),
                             ('probe',[str(probe),str(model),str(resources),str(folder)])]:
            result=subprocess.run(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=45,preexec_fn=limits,env=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2'))
            (folder/(kind+'.log')).write_text(result.stdout)
            receipt['runs'].append({'fixture':name,'kind':kind,'model_sha256':sha(model),'command':command,'exit_code':result.returncode,
                                    'stage_errors':dict(re.findall(r'STAGE (\w+) CRITERIA_ERRORS (\d+)',result.stdout))})
            (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    expected={'channel_valid':(0,'0'),'channel_invalid_length':(4,'1'),
              'hru_valid':(0,'0'),'hru_invalid_moisture':(4,'3')}
    passed=len(receipt['runs'])==8
    for item in receipt['runs']:
        code,count=expected[item['fixture']]
        if item['kind']=='console':passed &= item['exit_code']==code
        else:
            stages=item['stage_errors']
            passed &= (item['exit_code']==0 and {'constructed','initial_values'}<=set(stages)
                       and all(v==count for v in stages.values()))
    receipt['current_console_controls_passed']=passed
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))
    if expect_current and not passed:raise RuntimeError('Positive/negative console controls failed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['console','probe','resources','new_output']:parser.add_argument(name,type=Path)
    parser.add_argument('--expect-current',action='store_true',help='Fail unless valid cases pass and invalid controls remain rejected')
    a=parser.parse_args();run(a.console,a.probe,a.resources,a.new_output,a.expect_current)
