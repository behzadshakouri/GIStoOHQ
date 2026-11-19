#!/usr/bin/env python3
"""Real generator must reject a state-dependent block/link expression cycle."""
import argparse
import json
from pathlib import Path
import subprocess
from verify_ohq_example_compatibility import limits, sha


def run(generator,resources,output):
    generator,resources,output=(Path(p).resolve() for p in (generator,resources,output))
    output.mkdir(exist_ok=False,parents=True)
    templates=json.loads((resources/'open_channel.json').read_text())
    # Block velocity averages connected-link velocity; this adds the opposite
    # edge plus a state root. No forward solve is attempted for an invalid cycle.
    templates['Trapezoidal_Channel_link']['velocity']['expression']='velocity.s+Storage.s'
    local=output/'cyclic_channel.json';local.write_text(json.dumps(templates,indent=2)+'\n')
    model=output/'model.ohq';model.write_text(f'''loadtemplate;filename={resources}/main_components.json
addtemplate;filename={local}
create block;type=Trapezoidal Channel Segment,name=A,base_width=3,side_slope=2,ManningCoeff=.035,bottom_elevation=.5,length=100,depth=.05,inflow=,ag_area=0,non_ag_area=0,dam_height=0
create block;type=Trapezoidal Channel Segment,name=B,base_width=3,side_slope=2,ManningCoeff=.035,bottom_elevation=.3,length=100,depth=.05,inflow=,ag_area=0,non_ag_area=0,dam_height=0
create link;from=A,to=B,type=Trapezoidal_Channel_link,name=AB
setvalue;object=system,quantity=simulation_start_time,value=0
setvalue;object=system,quantity=simulation_end_time,value=.1
''')
    command=[str(generator),str(model),str(resources),str(output/'generated'),'CycleModel','--project','lib']
    with (output/'generation.log').open('w') as log:
        result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=15,preexec_fn=limits)
    log=(output/'generation.log').read_text()
    passed=result.returncode==2 and 'cyclic per-iteration dependencies' in log and 'AB::velocity' in log and 'A::velocity' in log
    receipt={'generator_sha256':sha(generator),'model_sha256':sha(model),'returncode':result.returncode,'cycle_rejected':passed,'command':command}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2));return passed


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('generator','resources','new_output'):p.add_argument(name,type=Path)
    a=p.parse_args();raise SystemExit(0 if run(a.generator,a.resources,a.new_output) else 1)
