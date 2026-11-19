#!/usr/bin/env python3
"""Real-loader synthetic rainfall volume/timing and malformed-input checks."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from analyze_ohq_generated_event import read_series, integral, sha


def run(binary, resources, output):
    binary, resources, output = map(lambda p: Path(p).resolve(), (binary, resources, output))
    output.mkdir(exist_ok=False, parents=True)
    cases = {
        'unequal': ('0,.02,0\n.02,.04,.002\n.04,.1,0\n', .002),
        'single': ('.02,.04,.002\n', .002),
        'gap': ('0,.01,.001\n.04,.06,.002\n', .003),
        'negative': ('0,.02,-.001\n', None),
        'overlap': ('0,.04,.001\n.02,.1,.001\n', None),
        'nan': ('0,.1,nan\n', None),
        'junk': ('0,.1,.002junk\n', None),
        'zero_width': ('0,0,.002\n', None),
        'extra_field': ('0,.1,.002,extra\n', None),
        'empty_start': (',.02,.001\n.02,.1,0\n', None),
    }
    receipt = {'binary_sha256': sha(binary), 'research_training_launched': False, 'cases': {}}
    for label, (rain_text, depth) in cases.items():
        folder = output/label
        folder.mkdir()
        rain = folder/'rain.csv'
        rain.write_text(rain_text)
        model = f'''loadtemplate; filename={resources}/main_components.json
addtemplate; filename={resources}/rainfall_runoff.json
create source;type=Precipitation,name=Rain,timeseries={rain}
create block;type=Catchment,name=A,area=1000,depth=0.01,ManningCoeff=0.035,Precipitation=Rain,Runoff_coeff=1,loss_coefficient=0,elevation=0
'''
        settings = dict(simulation_start_time=0, simulation_end_time=.1, initial_time_step=.007,
                        max_timestep_increase_factor=1, nr_block_tolerance=1e-8, nr_tolerance=1e-9)
        for key, value in settings.items():
            model += f'setvalue; object=system,quantity={key},value={value}\n'
        (folder/'model.ohq').write_text(model)
        with (folder/'solver.log').open('w') as log:
            completed = subprocess.run([str(binary), str(folder/'model.ohq'), str(folder), str(folder/'native.csv')],
                stdout=log, stderr=subprocess.STDOUT, timeout=30, env={**os.environ, 'OMP_NUM_THREADS': '1'})
        record = {'returncode': completed.returncode}
        if depth is None:
            if completed.returncode != 3:
                raise ValueError(f'{label}: expected construction/input rejection, not solver failure')
        else:
            if completed.returncode:
                raise ValueError(f'{label}: valid native rainfall rejected')
            series = read_series(folder/'native.csv')
            rain_volume = integral(series['A_Precipitation_runoff'])
            storage_change = series['A_Storage'][-1][1]-series['A_Storage'][0][1]
            expected = depth*1000
            if abs(rain_volume-expected) > 1e-8 or abs(storage_change-expected) > 1e-8:
                raise ValueError(f'{label}: rainfall or storage volume mismatch')
            # For the isolated pulse, fully dry solver intervals stay dry.
            if label == 'single':
                for before, after in zip(series['A_Precipitation_runoff'], series['A_Precipitation_runoff'][1:]):
                    if (after[0] <= .02 or before[0] >= .04) and abs(after[1]) > 1e-8:
                        raise ValueError('Rain leaked outside native interval')
            record.update(expected_m3=expected, rainfall_m3=rain_volume, storage_change_m3=storage_change)
        receipt['cases'][label] = record
    (output/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('diagnostic_binary', 'resources', 'new_output'):
        p.add_argument(name, type=Path)
    a = p.parse_args()
    run(a.diagnostic_binary, a.resources, a.new_output)
