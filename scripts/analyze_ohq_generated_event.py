#!/usr/bin/env python3
"""Audit native accepted-step OHQ whole-domain water outputs (synthetic only).

Rates are end-stamped step values: integrate right rectangles, not trapezoids.
Exclude fixed-head boundary storage; internal link transfers cancel globally.
This is a whole-domain check, not an individual HRU/link conservation proof.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_series(path):
    result = {}
    name = None
    for line in Path(path).read_text().splitlines():
        if line.startswith('#'):
            name = line[1:]
            if not name or name in result:
                raise ValueError('Duplicate/empty series')
            result[name] = []
        else:
            if name is None:
                raise ValueError('Missing series header')
            row = line.split(',')
            if len(row) != 2:
                raise ValueError('Malformed native row')
            t, v = map(float, row)
            if not math.isfinite(t) or not math.isfinite(v):
                raise ValueError('Nonfinite output')
            if result[name] and t <= result[name][-1][0]:
                raise ValueError('Unordered output')
            result[name].append((t, v))
    if not result or any(len(s) < 2 or not 0 <= s[0][0] <= .00100000001 or abs(s[-1][0]-.1) > 1e-10 for s in result.values()):
        raise ValueError('Incomplete synthetic event output')
    return result


def integral(series):
    return series[0][0]*series[0][1] + math.fsum((b[0]-a[0])*b[1] for a, b in zip(series, series[1:]))


def audit(folder, area_m2):
    folder = Path(folder)
    if not math.isfinite(area_m2) or area_m2 <= 0:
        raise ValueError('Invalid independent source area')
    series = read_series(folder/'native.csv')
    inventory = [s.split('\t') for s in (folder/'inventory.tsv').read_text().splitlines()]
    states = [r[1]+'_Storage' for r in inventory if r[0] == 'storage' and r[2] != 'fixed_head']
    external = [r for r in inventory if r[0] == 'external' and r[1] != 'Outlet']
    boundary = [r for r in inventory if r[0] == 'link' and (r[2] == 'Outlet' or r[3] == 'Outlet')]
    if not states or not external or len(boundary) != 1:
        raise ValueError('Unexpected generated-model topology')
    terms = {}
    for row in external:
        key = row[2]
        terms[key] = terms.get(key, 0.) + integral(series[row[1]+'_'+key])
    rain = math.fsum(v for k, v in terms.items() if k in ('Precipitation', 'Precipitation_runoff'))
    known = {'Precipitation', 'Precipitation_runoff', 'loss', 'Evapotranspiration', 'inflow', 'inflow_adjustment', 'ag_withdrawal', 'non_ag_withdrawal', 'recharge'}
    if set(terms)-known:
        raise ValueError('Unreviewed external balance term')
    row = boundary[0]
    outlet = integral(series[row[1]+'_flow']) * (1 if row[3] == 'Outlet' else -1)
    if any(abs(series[s][0][0]) > 1e-10 for s in states):
        raise ValueError('Missing initial storage')
    storage_change = math.fsum(series[s][-1][1]-series[s][0][1] for s in states)
    net_external = math.fsum(terms.values())
    expected_rain = area_m2*.002
    residual = net_external-outlet-storage_change
    # Predeclared event-development acceptance, not a performance metric.
    tolerance = .01+1e-6*expected_rain
    return dict(storage_compartments=len(states), duration_days=.1,
                independent_area_m2=area_m2, expected_rain_m3=expected_rain,
                solver_rain_m3=rain, external_terms_m3=terms,
                boundary_outflow_m3=outlet, storage_change_m3=storage_change,
                residual_m3=residual, tolerance_m3=tolerance,
                rainfall_volume_pass=abs(rain-expected_rain)<=tolerance,
                whole_domain_balance_pass=abs(residual)<=tolerance,
                input_sha256={p.name:sha(p) for p in (folder/'model.ohq', folder/'rain.csv', folder/'native.csv', folder/'inventory.tsv')})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('folder', type=Path)
    p.add_argument('--area-m2', type=float, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    receipt = audit(a.folder, a.area_m2)
    a.output.write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))
    raise SystemExit(0 if receipt['rainfall_volume_pass'] and receipt['whole_domain_balance_pass'] else 1)
