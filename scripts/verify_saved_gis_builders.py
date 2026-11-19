#!/usr/bin/env python3
"""Replay OHQ/HMS builders from saved GIS without new downloads or simulations."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(root,site,output):
    root,output=Path(root).resolve(),Path(output).resolve()
    if output.exists():raise FileExistsError(output)
    source=root/site/'outputs'
    names=['topology.gpkg','subwatershed_params.gpkg','reaches.gpkg','junctions.gpkg']
    before={name:digest(source/name) for name in names}
    output.mkdir(parents=True)
    receipt={'input_sha256':before,'simulations_run':False,'commands':[],
             'limitation':'Saved-GIS builder replay only; not fresh delineation, calibrated simulation, solver acceptance or interactive plugin verification.'}
    for label,args in [('ohq',['build','--out',str(output/'model.ohq')]),
                       ('hms',['build-hms','--out-dir',str(output/'hms')])]:
        command=[sys.executable,'-m','ohqbuilder.cli',*args,'--root',str(root),'--site',site,'--project-name','SavedGISBuilderReplay']
        with (output/(label+'.log')).open('x') as log:
            p=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=120)
        receipt['commands'].append({'command':command,'exit_code':p.returncode})
        if p.returncode:
            (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
            raise RuntimeError(label+' failed; inspect saved log')
    ohq=list(output.glob('*.ohq'))
    if len(ohq)!=4 or any(not p.stat().st_size for p in ohq):raise RuntimeError('Expected four nonempty OHQ formulations')
    from ohqbuilder.hms_pipeline import validate_hms_project
    projects=list((output/'hms').glob('*.hms'))
    if len(projects)!=1:raise RuntimeError('Expected one native HMS index')
    referenced=validate_hms_project(projects[0])
    if before!={name:digest(source/name) for name in names}:raise RuntimeError('Original GIS changed')
    receipt.update(ohq_formulations=[p.name for p in ohq],hms_references=len(referenced),input_unchanged=True)
    receipt['output_sha256']={str(p.relative_to(output)):digest(p) for p in output.rglob('*') if p.is_file()}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path);parser.add_argument('site');parser.add_argument('new_output',type=Path)
    args=parser.parse_args();print(json.dumps(verify(args.root,args.site,args.new_output),indent=2))
