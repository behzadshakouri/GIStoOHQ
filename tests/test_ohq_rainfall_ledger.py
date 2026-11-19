import pytest
from scripts.verify_ohq_rainfall_ledger import check,read_columns


def columns():
    def s(a,b):return [(0.,a),(.1,b)]
    return {'A_Precipitation':s(10,10),'B_Precipitation':s(25,25),
            'A_Storage':s(10,10.99),'B_Storage':s(25,27.48),
            'Collector_Storage':s(0,.03),'A_loss':s(0,0),'B_loss':s(0,0),
            'A_Precipitation_loss':s(0,0),'B_Precipitation_loss':s(0,0)}


def test_known_rainfall_receiver_areas_and_closed_ledger():
    result=check(columns(),(1000,2500),1,0)
    assert result['expected_rain_m3']==3.5
    assert abs(result['mass_residual_m3'])<1e-12


def test_shared_source_cannot_reuse_the_other_receivers_area():
    c=columns();c['B_Precipitation']=[(0,10),(.1,10)]
    with pytest.raises(ValueError,match='receiving area'):check(c,(1000,2500),1,0)


@pytest.mark.parametrize('value',[float('nan'),float('inf')])
def test_nonfinite_values_cannot_hide_behind_comparisons(value):
    c=columns();c['A_Precipitation'][0]=(0,value)
    with pytest.raises(ValueError,match='Nonfinite'):check(c,(1000,2500),1,0)


def test_wrong_storage_cannot_pass_a_balanced_looking_rainfall_table():
    c=columns();c['A_Storage'][-1]=(.1,11.99)
    with pytest.raises(ValueError,match='analytic'):check(c,(1000,2500),1,0)


def test_incorrect_loss_diagnostic_reopens_mass_ledger():
    c=columns();c['A_loss']=[(0,-1),(.1,-1)]
    with pytest.raises(ValueError,match='ledger'):check(c,(1000,2500),1,0)


def test_native_output_parser_rejects_nan(tmp_path):
    p=tmp_path/'output.csv';p.write_text('t,A_Storage\n0,10\n0.1,nan\n')
    with pytest.raises(ValueError,match='Nonfinite'):read_columns(p)
