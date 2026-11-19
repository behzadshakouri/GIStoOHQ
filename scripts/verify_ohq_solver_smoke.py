#!/usr/bin/env python3
"""Bounded dry-start solver smoke on COPIES of generated OHQ formulations.

No calibration, observations, parameter fitting or production output mutation.
The shortened unforced run tests construction/solver startup, not hydrographs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import subprocess


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


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
        command = [str(binary), str(model), '--workdir', str(folder), '--resources', str(resources), '--quiet']
        with (folder/'solver.log').open('x') as log:
            try:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=75,
                                        preexec_fn=limits, env=dict(os.environ, OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2'))
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 'timeout'
        assert sha(source) == before, 'Original builder output changed'
        receipt['runs'].append({'model': source.name, 'source_sha256': before, 'smoke_sha256': sha(model),
                                'command': command, 'exit_code': code})
        (output/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))
    return all(item['exit_code'] == 0 for item in receipt['runs'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('binary','resources','generated_models','new_output'): parser.add_argument(name,type=Path)
    a = parser.parse_args()
    raise SystemExit(0 if run(a.binary,a.resources,a.generated_models,a.new_output) else 1)
