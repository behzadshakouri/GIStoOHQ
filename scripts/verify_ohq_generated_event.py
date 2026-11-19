#!/usr/bin/env python3
"""Run bounded synthetic events on isolated full generated OHQ formulations.

Requires the diagnostic console built from ohq_generated_event_probe.cpp using
relink_ohq_snapshot_variant.py, not the installed application console.
Never loads observations, fits parameters, or edits original model files.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from analyze_ohq_generated_event import audit, sha


def run(models, binary, output):
    models, binary, output = map(lambda x: Path(x).resolve(), (models, binary, output))
    output.mkdir(exist_ok=False, parents=True)
    receipt = {'diagnostic_binary_sha256': sha(binary), 'cases': [], 'research_training_launched': False}
    for mode in ('legacy', 'standard_hru', 'mixed_hru', 'riparian_hru'):
        source = models/f'PluginAcceptance_{mode}.ohq'
        text = source.read_text()
        if re.search(r'^\s*create observation;', text, re.M):
            raise ValueError('Observation-bearing model is outside synthetic audit scope')
        # Independent sum of the generator's top-level subbasin input areas.
        areas = re.findall(r'^create composite;[^\n]*?name=Subbasin_\d+,[^\n]*?\barea=([0-9.eE+-]+)\[m~\^2\]', text, re.M)
        if len(areas) != 29:
            raise ValueError('Expected 29 preserved generated subbasins')
        area = sum(map(float, areas))
        original_hash = sha(source)
        for equal in (False, True):
            label = mode+('_equal_intervals' if equal else '')
            case = output/label
            case.mkdir()
            rain = case/'rain.csv'
            rain.write_text(''.join(f'{i/100:.2f},{(i+1)/100:.2f},{.001 if i in (2,3) else 0}\n' for i in range(10)) if equal else '0,0.02,0\n0.02,0.04,0.002\n0.04,0.1,0\n')
            new = text+f'\nsetvalue; object=Rain, quantity=timeseries, value={rain}\n'
            settings = dict(simulation_end_time=.1, initial_time_step=.001,
                            max_timestep_increase_factor=1, c_n_weight=.5, nr_tolerance=1e-9)
            if equal:
                settings['nr_block_tolerance'] = 1e-8
            for key, value in settings.items():
                new += f'setvalue; object=system, quantity={key}, value={value}\n'
            (case/'model.ohq').write_text(new)
            with (case/'solver.log').open('w') as log:
                result = subprocess.run([str(binary), str(case/'model.ohq'), str(case), str(case/'native.csv')], stdout=log, stderr=subprocess.STDOUT, timeout=180, env={**os.environ, 'OMP_NUM_THREADS': '1'})
            if result.returncode:
                raise RuntimeError(f'{label} failed: exit {result.returncode}')
            data = audit(case, area)
            data.update(source_model=str(source), source_sha256=original_hash,
                        equal_intervals=equal, numerical_settings=settings)
            (case/'ledger.json').write_text(json.dumps(data, indent=2)+'\n')
            receipt['cases'].append({'case': label, **data})
        if sha(source) != original_hash:
            raise ValueError('Original generated model changed')
    receipt['all_cases_pass'] = all(c['rainfall_volume_pass'] and c['whole_domain_balance_pass'] for c in receipt['cases'])
    (output/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('models', type=Path)
    p.add_argument('diagnostic_binary', type=Path)
    p.add_argument('new_output', type=Path)
    a = p.parse_args()
    result = run(a.models, a.diagnostic_binary, a.new_output)
    raise SystemExit(0 if result['all_cases_pass'] else 1)
