#!/usr/bin/env python3
"""Bounded isolated forward-only OHQ before/after example compatibility audit."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import time


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read_output(path, start, end):
    with Path(path).open() as stream:
        rows = list(csv.reader(stream))
    if len(rows)<3 or len(rows[0])%2:
        raise ValueError('Missing/invalid paired solver output')
    names = [s.strip() for s in rows[0][1::2]]
    if len(set(names)) != len(names): raise ValueError('Duplicate quantities')
    values=[]
    for k in range(0,len(rows[0]),2):
        series=[]
        for row in rows[1:]:
            t,v=float(row[k]),float(row[k+1])
            if not math.isfinite(t) or not math.isfinite(v): raise ValueError('Nonfinite solver output')
            if series and t<=series[-1][0]: raise ValueError('Unordered solver output')
            series.append((t,v))
        # Console writer prints times with five decimal places, even for Julian days.
        tol=1e-5
        if abs(series[0][0]-start)>tol or abs(series[-1][0]-end)>tol:
            raise ValueError('Incomplete solver output support')
        values.append(series)
    return dict(quantity_count=len(names), rows=len(rows)-1)


def limits():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
    os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))


def run(repo, before, after, output):
    repo,before,after,output=map(lambda p:Path(p).resolve(),(repo,before,after,output))
    output.mkdir(exist_ok=False,parents=True)
    resources=repo/'resources'
    receipt={'before_binary_sha256':sha(before),'after_binary_sha256':sha(after),
        'cases':[],'research_fits':0,'original_sha256':{}}
    examples=[('Draining_Tank/Draining_Tank.ohq',None),('Parallel_Links/Parallel_Links.ohq',None),
      ('Channel_Test/Channel_Test.ohq',None),('Fixed_Head_Test/Fixed_Head_Test.ohq',None),
      ('Reservoir_Rule/Reservoir_Rule.ohq',None),('Linear_Baseflow/Linear_Baseflow.ohq',None),
      ('Snowmelt/snowmelt.ohq',None),('CN_Catchment/CN_watershed.ohq',2),
      ('Hydrologic_Response_Unit/HRU_hillslope.ohq',2),('Urban_HRU/Urban_HRU_example.ohq',2),
      ('Plant_Transpiration/rooted_soil_profile_penman.ohq',2),
      ('Crop_Root_Uptake/crop_root_uptake.ohq',7),('Bioretention/Bioretention_Model.ohq',2)]
    for relative, cap in examples:
        source=repo/'Examples'/relative
        label=source.parent.name
        case=output/label;case.mkdir()
        text=source.read_text();receipt['original_sha256'][str(source)]=sha(source)
        # Remove observed-data/objective/parameter declarations: forward physics
        # only. No observational files, fitting or parameter proposal is loaded.
        lines=[s for s in text.splitlines() if not re.match(r'\s*create (observation|objectivefunction|parameter);',s,re.I)]
        text='\n'.join(lines)+'\n'
        def template(m):
            name=Path(m.group(2).strip()).name
            return m.group(1)+str(resources/name)
        text=re.sub(r'(?m)^((?:loadtemplate|addtemplate);\s*filename\s*=\s*)([^\n]+)',template,text)
        # Explicitly copy only repo-local files referenced by existing input
        # tokens. Never follow an external Dropbox/research model reference.
        for token in set(re.findall(r'=\s*([^,\n;]+\.(?:csv|txt))(?=,|\n|$)',text,re.I)):
            candidate=(source.parent/token).resolve()
            if not candidate.is_relative_to(repo):raise ValueError('External data reference outside repository')
            if candidate.is_file():
                dest=case/candidate.name;shutil.copy2(candidate,dest)
                receipt['original_sha256'][str(candidate)]=sha(candidate)
                text=text.replace(token,str(dest))
        get=lambda key:float(re.findall(r'quantity\s*=\s*'+key+r'\s*,\s*value\s*=\s*([^,\s]+)',text)[-1])
        start,end=get('simulation_start_time'),get('simulation_end_time')
        if cap:end=min(end,start+cap)
        settings=dict(simulation_end_time=end,alloutputfile='output.csv',observed_outputfile='',
            record_results='Yes',write_solution_details='No',maximum_time_allowed=90,
            n_threads=1,number_of_threads=1)
        text+='\n'+''.join(f'setvalue; object=system,quantity={k},value={v}\n' for k,v in settings.items())
        record={'case':label,'source':str(source),'start':start,'end':end,
                'shortened':cap is not None,'native_precipitation_present':'type=Precipitation' in text or 'type=Snowfall' in text}
        for name,binary in [('before',before),('after',after)]:
            folder=case/name;folder.mkdir();model=folder/'model.ohq';model.write_text(text)
            command=[str(binary),str(model),'--resources',str(resources),'--workdir',str(folder)]
            t=time.monotonic()
            with (folder/'solver.log').open('w') as log:
                try:p=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=100,preexec_fn=limits,
                    env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
                except subprocess.TimeoutExpired:
                    record[name]={'timeout':True};continue
            result={'returncode':p.returncode,'seconds':time.monotonic()-t,'model_sha256':sha(model)}
            if p.returncode==0:
                try:result.update(read_output(folder/'output.csv',start,end));result['output_sha256']=sha(folder/'output.csv')
                except (ValueError,IndexError,FileNotFoundError) as e:result['output_error']=str(e)
            record[name]=result
        b,a=record['before'],record['after']
        valid=lambda d:d.get('returncode')==0 and 'output_sha256' in d
        record['both_valid']=valid(b) and valid(a)
        record['byte_identical']=record['both_valid'] and b['output_sha256']==a['output_sha256']
        print(label,json.dumps({k:record[k] for k in ('both_valid','byte_identical')}),flush=True)
        receipt['cases'].append(record)
        (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    receipt['originals_unchanged']=all(sha(p)==h for p,h in receipt['original_sha256'].items())
    if not receipt['originals_unchanged']:raise ValueError('Original input changed')
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('repo','before','after','new_output'):p.add_argument(n,type=Path)
    a=p.parse_args();receipt=run(a.repo,a.before,a.after,a.new_output)
    raise SystemExit(0 if receipt.get('originals_unchanged') and receipt['cases'] and all(c['both_valid'] for c in receipt['cases']) else 1)
