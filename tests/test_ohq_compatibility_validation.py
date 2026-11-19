import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('compatibility',Path(__file__).parents[1]/'scripts/verify_ohq_example_compatibility.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_reject_truncated_calendar_output_despite_large_epoch(tmp_path):
    p=tmp_path/'output.csv'
    p.write_text('t,Storage\n36526,1\n36527,2\n36527.99,3\n')
    with pytest.raises(ValueError,match='Incomplete'):
        m.read_output(p,36526,36528)


@pytest.mark.parametrize('text',['t,Storage\n0,1\n.5,nan\n1,3\n','t,Storage\n0,1\n.5,2\n.5,3\n','t,Storage,t,Storage\n0,1,0,1\n.5,2,.5,2\n1,3,1,3\n'])
def test_reject_invalid_solver_evidence(tmp_path,text):
    p=tmp_path/'output.csv';p.write_text(text)
    with pytest.raises(ValueError):m.read_output(p,0,1)


def test_accept_complete_finite_calendar_output(tmp_path):
    p=tmp_path/'output.csv'
    p.write_text('t,Storage\n36526,1\n36527,2\n36528,3\n')
    assert m.read_output(p,36526,36528)=={'quantity_count':1,'rows':3}


def test_external_forcing_reference_is_blocked_before_execution(tmp_path):
    repo=tmp_path/'repo'
    example=repo/'Examples/Draining_Tank/Draining_Tank.ohq'
    example.parent.mkdir(parents=True)
    outside=tmp_path/'external.csv';outside.write_text('0,1,.01\n')
    example.write_text(f'create source;type=Precipitation,name=Rain,timeseries={outside}\n')
    binary=tmp_path/'unusable_binary';binary.write_text('Not executable: must never be launched')
    with pytest.raises(ValueError,match='External data reference'):
        m.run(repo,binary,binary,tmp_path/'audit')
