from types import SimpleNamespace
from decimal import Decimal
import pytest
from das_workbench.public_selection import time_snapshot,absolute_interval,timestamp_offset
from das_workbench.public_ui import PublicWindow
from validation.public_timestamp import exercise
from test_public_native import asn

@pytest.mark.parametrize('dt,n',[(.01375,727),(.00155,199982),(.001759922562478001,5682),(.0002,166369)])
def test_timestamp_decimal_bounds_and_roundtrip(dt,n):
    s=SimpleNamespace(dt=dt,n=n,origin='2021-11-04T00:00:02.025Z')
    for a in sorted(set([1,*[i for i in [1000,5000] if i+100<=n],n-100])):
        t=time_snapshot(s,a,a+100)
        for _ in range(10):
            assert absolute_interval(s,t['timestamp'],t['duration_display'],t)==(a,a+100)
            t=time_snapshot(s,a,a+100)
        assert Decimal(t['duration_exact'])==100*Decimal(str(dt))
    t=time_snapshot(s,1,101)
    txt=time_snapshot(s,Decimal('1.25'),Decimal('101.25'))['timestamp']
    assert absolute_interval(s,txt,t['duration_display'],t)==(2,102)
    assert absolute_interval(s,t['timestamp'],str(Decimal('102.25')*Decimal(str(dt))),t)==(1,104)
    for txt in ['bad','1900-01-01T00:00:00Z',time_snapshot(s,n,n+1)['timestamp']]:
        with pytest.raises(ValueError):absolute_interval(s,txt,t['duration_display'],t)
    t=time_snapshot(s,n-100,n)
    with pytest.raises(ValueError):absolute_interval(s,t['timestamp'],str(101*Decimal(str(dt))),t)


def test_timestamp_nanoseconds_do_not_snap_edited_inputs():
    s=SimpleNamespace(dt=.001,n=100,origin='2020-01-01T00:00:00Z')
    assert absolute_interval(s,'2020-01-01T00:00:00.001000001Z','.01')==(2,12)
    assert absolute_interval(s,'2020-01-01T00:00:00.000999999Z','.01')==(1,11)
    assert timestamp_offset('2020-01-01T01:00:00.001000001+01:00',s.origin)==Decimal('.001000001')
    with pytest.raises(ValueError,match='timezone'):absolute_interval(s,'2020-01-01T00:00:00.001','.01')
    s.origin='2020-01-01T00:00:00';t=time_snapshot(s,1,11)
    assert t['timestamp']=='2020-01-01T00:00:00.001000000'
    assert absolute_interval(s,t['timestamp'],t['duration_display'],t)==(1,11)
    with pytest.raises(ValueError,match='timezone'):absolute_interval(s,t['timestamp']+'Z','.01',t)
    with pytest.raises(ValueError):absolute_interval(s,'2020-01-01T00:00:00.0000000001','.01')


def test_actual_qt_timestamp_button_errors_reviews_and_durations(tmp_path,qtbot,qapp):
    p=tmp_path/'asn.h5';asn(p);w=PublicWindow();qtbot.addWidget(w);w.open_recording(p)
    w.signals[0].source['sha256']='a'*64;w.signals[0].dt=.001759922562478001
    # Header-only fixture enlarged to exercise selection (this test never reads those samples).
    w.signals[0].shape=(5682,w.signals[0].c)
    exercise(w,qapp,tmp_path)
