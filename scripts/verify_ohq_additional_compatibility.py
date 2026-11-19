#!/usr/bin/env python3
"""Isolated sorption/template and culvert regression checks against two binaries."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from verify_ohq_example_compatibility import sha, read_output, limits


def command(args, cwd, log, env, timeout=120):
    with log.open('w') as stream:
        result = subprocess.run(list(map(str,args)), cwd=cwd, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=timeout, preexec_fn=limits)
    return result.returncode


def run(repo, before, after, output, suites=('envp_regressions','sorption_basis','culvert')):
    repo,before,after,output=(Path(p).resolve() for p in (repo,before,after,output))
    output.mkdir(parents=True,exist_ok=False)
    resources=repo/'resources'
    env={**os.environ,'OHQ_RESOURCES':str(resources),'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'}
    receipt={'before_sha256':sha(before),'after_sha256':sha(after),'input_sha256':{},'cases':[],'suites':{}}
    # Copy only tracked test scripts and local fixture inputs, never old outputs.
    for suite in suites:
        source=repo/('Examples/Culvert' if suite=='culvert' else 'tests/'+suite)
        files=(sorted(source.glob('T*.ohq'))+sorted(source.glob('*.csv')) if suite=='culvert' else [source/'build_models.py',source/'check_results.py'])
        for file in files:receipt['input_sha256'][str(file)]=sha(file)
        group=output/suite;group.mkdir()
        for label,binary in (('before',before),('after',after)):
            folder=group/label;folder.mkdir()
            for file in files:shutil.copy2(file,folder/file.name)
            if suite!='culvert':
                if command(['python3',folder/'build_models.py',resources],folder,folder/'build_models.log',env)!=0:
                    raise ValueError('Test fixture generation failed')
                modeldir=folder/'generated'
            else:modeldir=folder
            models=sorted(modeldir.glob('*.ohq'))
            if not models:raise ValueError('Empty test suite')
            for model in models:
                text=model.read_text()
                # Retain all native model settings/physics; bound wall-clock only.
                if re.search(r'create\s+(observation|objectivefunction|parameter)\s*;',text,re.I):
                    raise ValueError('Forward-only fixtures required')
                try:
                    rc=command([binary,model,'-r',resources],modeldir,model.with_suffix('.console.log'),env,timeout=30 if suite=='culvert' else 120)
                except subprocess.TimeoutExpired:
                    rc=124
                # Existing envp checker expects this specific filename.
                if suite=='envp_regressions':shutil.copy2(model.with_suffix('.console.log'),modeldir/(model.stem+'_console.log'))
                get=lambda key:float(re.findall(r'quantity\s*=\s*'+key+r'\s*,\s*value\s*=\s*([^,\s]+)',text)[-1])
                name=re.findall(r'quantity\s*=\s*alloutputfile\s*,\s*value\s*=\s*([^,\s]+)',text)[-1]
                path=modeldir/name
                result={'case':suite+'/'+model.stem,'variant':label,'returncode':rc}
                if rc==0:
                    try:result.update(read_output(path,get('simulation_start_time'),get('simulation_end_time')));result['output_sha256']=sha(path)
                    except (ValueError,IndexError,OSError) as e:result['output_error']=str(e)
                receipt['cases'].append(result)
                (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
                print(result['case'],label,'rc',rc,'complete', 'output_sha256' in result,flush=True)
            if suite!='culvert':
                # Analytical checkers have real failure exits (including the
                # sorption negative-control expectation); not a report-only check.
                rc=command(['python3',folder/'check_results.py'],folder,folder/'analytical_checks.log',env)
                receipt['suites'][suite+'/'+label]={'analytical_returncode':rc}
    pairs={}
    for case in receipt['cases']:pairs.setdefault(case['case'],{})[case['variant']]=case
    receipt['comparisons']={name:{'both_complete':all(v.get('returncode')==0 and 'output_sha256' in v for v in pair.values()) and len(pair)==2,
        'byte_identical':len(pair)==2 and pair['before'].get('output_sha256') is not None and pair['before'].get('output_sha256')==pair['after'].get('output_sha256')} for name,pair in pairs.items()}
    receipt['inputs_unchanged']=all(sha(p)==h for p,h in receipt['input_sha256'].items())
    receipt['all_pass']=receipt['inputs_unchanged'] and all(v['both_complete'] and v['byte_identical'] for v in receipt['comparisons'].values()) and all(v['analytical_returncode']==0 for v in receipt['suites'].values())
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('repo','before','after','new_output'):parser.add_argument(name,type=Path)
    parser.add_argument('--suites',nargs='+',choices=('envp_regressions','sorption_basis','culvert'),default=('envp_regressions','sorption_basis','culvert'))
    args=parser.parse_args();result=run(args.repo,args.before,args.after,args.new_output,args.suites)
    raise SystemExit(0 if result['all_pass'] else 1)
