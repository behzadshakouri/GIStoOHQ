import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('event_ledger', Path(__file__).parents[1]/'scripts/analyze_ohq_generated_event.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_end_stamped_rates_include_first_interval():
    assert m.integral([(.001, 10.), (.002, 20.)]) == pytest.approx(.03)
    assert m.integral([(0., 999.), (.001, 10.), (.002, 20.)]) == pytest.approx(.03)


@pytest.mark.parametrize('text', ['#a\n0,nan\n.1,1\n', '#a\n0,1\n0,2\n', '#a\n0,1\n.05,2\n', '#a\n0,1\n.1,2\n#a\n0,1\n.1,2\n', '0,1\n.1,2\n', '#a\n.01,1\n.1,2\n'])
def test_reject_invalid_native_outputs(tmp_path, text):
    p = tmp_path/'native.csv'
    p.write_text(text)
    with pytest.raises(ValueError):
        m.read_series(p)


def fixture(tmp_path, rain=2.):
    (tmp_path/'inventory.tsv').write_text('storage\tA\tCatchment\t0\nexternal\tA\tPrecipitation\nlink\tOut\tA\tOutlet\n')
    (tmp_path/'native.csv').write_text(f'#A_Storage\n0,1\n.1,1.1\n#A_Precipitation\n0,{rain}\n.1,{rain}\n#Out_flow\n.001,1\n.1,1\n')
    (tmp_path/'model.ohq').write_text('synthetic fixture')
    (tmp_path/'rain.csv').write_text('0,.1,.002')


def test_real_storage_and_boundary_terms(tmp_path):
    fixture(tmp_path)
    result = m.audit(tmp_path, 100.)
    assert result['rainfall_volume_pass']
    assert result['whole_domain_balance_pass']
    assert result['residual_m3'] == pytest.approx(0., abs=1e-14)


def test_closure_does_not_replace_independent_rainfall_check(tmp_path):
    fixture(tmp_path, rain=3.)
    (tmp_path/'native.csv').write_text((tmp_path/'native.csv').read_text().replace('.1,1.1', '.1,1.2'))
    result = m.audit(tmp_path, 100.)
    assert result['whole_domain_balance_pass']
    assert not result['rainfall_volume_pass']
