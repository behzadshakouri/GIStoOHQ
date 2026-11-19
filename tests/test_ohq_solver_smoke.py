import pytest
from scripts.verify_ohq_solver_smoke import verify_output


def test_real_solver_style_trajectory_requires_finite_complete_time_support(tmp_path):
    path=tmp_path/'output.txt'
    path.write_text('t,depth,t,Storage\n0,0.01,0,3.02\n0.001,0.009,0.001,2.71\n')
    assert verify_output(path)['trajectory_rows']==2


@pytest.mark.parametrize('data',[
    't,depth\n0,0.01\n',
    't,depth\n0,0.01\n0.001,nan\n',
    't,depth\n0,0.01\n0.001,inf\n',
    't,depth\n0,0.01\n0.0005,0.009\n',
    't,depth,t,Storage\n0,0.01,0,3.02\n0.001,0.009,0.002,2.71\n',
    't,depth\n0,0.01\n0.001\n',
])
def test_exit_zero_cannot_hide_missing_nonfinite_or_incomplete_solver_output(tmp_path,data):
    path=tmp_path/'output.txt';path.write_text(data)
    with pytest.raises(ValueError):verify_output(path)
