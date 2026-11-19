#!/usr/bin/env python3
"""Bounded dry-start solver smoke on COPIES of generated OHQ formulations.

No calibration, observations, parameter fitting or production output mutation.
The shortened unforced run tests construction/solver startup, not hydrographs.
"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import subprocess


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def verify_output(path):
    with Path(path).open() as stream:
        rows=list(csv.reader(stream))
    if len(rows)<3 or not rows[0] or len(rows[0])%2:
        raise ValueError('Missing solver trajectory or malformed time/value columns')
    if any(c.strip()!='t' for c in rows[0][::2]):
        raise ValueError('Expected explicit time/value pairs')
    values=[]
    for row in rows[1:]:
        if len(row)!=len(rows[0]):raise ValueError('Ragged solver output')
        parsed=list(map(float,row))
        if not all(math.isfinite(v) for v in parsed):raise ValueError('Nonfinite solver trajectory')
        if any(abs(t-parsed[0])>1e-9 for t in parsed[::2]):raise ValueError('Output columns disagree on time')
        values.append(parsed)
    if abs(values[0][0])>1e-9 or abs(values[-1][0]-.001)>1e-9 or any(b[0]<=a[0] for a,b in zip(values,values[1:])):
        raise ValueError('Incomplete or unordered solver time support')
    return {'trajectory_rows':len(values),'numeric_columns':len(rows[0]),'output_sha256':sha(path)}


def run(binary, resources, models, output):
    binary, resources, models, output = map(lambda p: Path(p).resolve(), (binary, resources, models, output))
    output.mkdir(parents=True, exist_ok=False)
    inputs = sorted(models.glob('model_*.ohq'))
    if len(inputs) != 4: raise ValueError('Exactly four generated formulations required')
    receipt = {'binary_sha256': sha(binary), 'runs': [], 'synthetic_dry_start_only': True,
               'limitations': 'Shortened unforced generated-model copies. Not calibrated simulation, rainfall response or scientific skill acceptance.'}
    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (4*1024**3, 4*1024**3))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        os.sched_setaffinity(0, set(sorted(os.sched_getaffinity(0))[:2]))
    for source in inputs:
        before = sha(source)
        folder = output / source.stem
        folder.mkdir()
        text, count = re.subn(r'(quantity=simulation_end_time, value=)1\s*$', r'\g<1>0.001', source.read_text(), flags=re.M)
        if count != 1: raise ValueError('Expected explicit default one-day simulation end')
        model = folder / source.name
        model.write_text(text)
        command = [str(binary), str(model), '--workdir', str(folder), '--resources', str(resources)]
        with (folder/'solver.log').open('x') as log:
            try:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=75,
                                        preexec_fn=limits, env=dict(os.environ, OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2'))
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 'timeout'
        assert sha(source) == before, 'Original builder output changed'
        item={'model': source.name, 'source_sha256': before, 'smoke_sha256': sha(model),
              'command': command, 'exit_code': code}
        if code==0:
            try:item.update(verify_output(folder/(source.stem+'_OHQ_output.txt')))
            except (OSError,ValueError) as exc:item['output_error']=str(exc)
        receipt['runs'].append(item)
        (output/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))
    return all(item['exit_code'] == 0 and 'output_sha256' in item for item in receipt['runs'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('binary','resources','generated_models','new_output'): parser.add_argument(name,type=Path)
    a = parser.parse_args()
    raise SystemExit(0 if run(a.binary,a.resources,a.generated_models,a.new_output) else 1)
