#!/usr/bin/env python3
"""Generate, compile and check actual kernels against the native interpreter."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from relink_ohq_snapshot_variant import relink, sha


def run(generator, snapshot, precipitation, examples, output, names=None):
    generator,snapshot,precipitation,examples,output=map(lambda p:Path(p).resolve(),(generator,snapshot,precipitation,examples,output))
    output.mkdir(exist_ok=False,parents=True)
    resources=Path('/mnt/3rd900/Projects/OpenHydroQual/resources')
    receipt={'generator_sha256':sha(generator),'cases':{}}
    allowed=('Draining_Tank','Parallel_Links','Reservoir_Rule','Snowmelt','Hydrologic_Response_Unit','Urban_HRU','Crop_Root_Uptake','Plant_Transpiration')
    if names and set(names)-set(allowed):raise ValueError('Unknown compatibility case')
    for name in (names or allowed):
        case=output/name;case.mkdir()
        model=examples/name/'after/model.ohq'
        gen=case/'generated'
        command=[str(generator),str(model),str(resources),str(gen),'CompatibilityModel','--project','lib']
        with (case/'generation.log').open('w') as log:
            r=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=120)
        if r.returncode:
            receipt['cases'][name]={'generation_returncode':r.returncode};continue
        for p in (gen/'runtime').glob('*.h'):shutil.copy2(p,gen/p.name)
        wrapper=case/'main.cpp'
        harness=Path(__file__).with_name('ohq_compatibility_parity.cpp')
        wrapper.write_text(f'#define GEN_HEADER "{gen}/CompatibilityModel.h"\n#define GEN_CLASS CompatibilityModel\n#include "{harness}"\n')
        harness_digest=sha(harness)
        # Source-pinned core and exactly one matching parser-object override;
        # all compilation/link commands and object hashes saved by relink().
        try:relink(snapshot,wrapper,case/'build',precipitation)
        except subprocess.CalledProcessError:
            receipt['cases'][name]={'compile_failed':True};continue
        if sha(harness)!=harness_digest:raise ValueError('Parity harness changed during build')
        with (case/'parity.log').open('w') as log:
            try:r=subprocess.run([str(case/'build/console'),str(model),str(resources)],stdout=log,stderr=subprocess.STDOUT,timeout=120)
            except subprocess.TimeoutExpired:
                receipt['cases'][name]={'timeout':True};continue
        receipt['cases'][name]={'returncode':r.returncode,'binary_sha256':sha(case/'build/console'),
            'generated_header_sha256':sha(gen/'CompatibilityModel.h'),'harness_sha256':harness_digest,'runtime_sha256':{p.name:sha(p) for p in (gen/'runtime').glob('*.h')},'command':command}
        print(name,r.returncode,flush=True)
        (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('generator','snapshot','precipitation','examples','new_output'):p.add_argument(n,type=Path)
    p.add_argument('--cases',nargs='+')
    a=p.parse_args();receipt=run(a.generator,a.snapshot,a.precipitation,a.examples,a.new_output,a.cases)
    raise SystemExit(0 if receipt['cases'] and all(c.get('returncode')==0 for c in receipt['cases'].values()) else 1)
