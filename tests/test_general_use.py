"""Semantic regressions: compatible schema is not documentary evidence."""
from pathlib import Path
from types import SimpleNamespace
import json
import h5py,numpy as np,pytest
from scipy.io import savemat
from das_workbench.native import open_recording
from das_workbench.public_ui import PublicWindow
from das_workbench.public_figures import figure,caption,export
from das_workbench.public_engine import analyse
from das_workbench.presentation import verification_html,concise_error
from test_public_native import asn,recipe


def test_unrelated_asn_has_no_example_findings(tmp_path):
    p=tmp_path/'unknown.h5';asn(p);s=open_recording(p)[0]
    text=json.dumps(s.summary())
    assert all(t not in text for t in ['README','MedSea','Trondheim','AIS','vessel','whale'])
    assert s.unit=='rad/(s*m)' and s.metadata['interpretation_state']=='declared'
    assert 'not established' in s.clock and not s.origin.endswith(('+00:00','Z'))
    with h5py.File(p,'a') as f:
        del f['header/name'];del f['header/unit'];f['header/name']='Velocity';f['header/unit']='m/s'
    s=open_recording(p)[0];assert s.quantity=='Velocity' and s.unit=='m/s' and s.metadata['interpretation_state']=='declared'
    assert np.array_equal(s.read(0,4,[0]),np.zeros((4,1)))


@pytest.mark.parametrize('units,expected',[(['nanostrain','second','meter'],'nanostrain'),(['volts','second','meter'],'native unit'),(['data: nanostrain (1e-6)','time: second','space: meter'],'native unit')])
def test_unrelated_mat_units_and_clock(tmp_path,units,expected):
    p=tmp_path/'new.mat';x=np.arange(300,dtype=float).reshape(3,100);dt=.01
    savemat(p,dict(data=x,x1_absolute_channel=[10,11,12],x1_relative_channel=[0,1,2],x1_position_m=[20,22,24],x1_distance_from_shore_m=[10,12,14],x2_time_s=np.arange(100)*dt,info_sample_interval_s=dt,info_sampling_frequency_Hz=100,info_GL_m=8,info_SSI_m=2,info_timestamp='20200101 12:00:00',info_units=units))
    s=open_recording(p)[0];assert s.unit==expected;assert 'timezone' in s.clock and not s.origin.endswith('Z');assert not s.pressure
    assert s.metadata['documented_source'] is None
    np.testing.assert_array_equal(s.read(0,20,[0,1]),x[:2,:20].T)


def test_unrelated_32600_channel_title(qtbot,monkeypatch):
    from das_workbench import ui
    monkeypatch.setattr(ui.OverviewDialog,'update_estimate',lambda self:None)
    run=SimpleNamespace(first=SimpleNamespace(channels=32600,spacing_m=2.042,start_locus=0))
    w=ui.OverviewDialog(run,0,1);qtbot.addWidget(w);assert w.title.text()=='DAS | full-fibre overview'


def test_unreviewed_figures_no_event_and_readable_verification(tmp_path,qtbot):
    p=tmp_path/'unknown.h5';asn(p);s=open_recording(p)[0];d=tmp_path/'result';analyse(p,recipe(s),d)
    f=figure(d);texts=[t.get_text() for t in f.texts]+[a.get_title() for a in f.axes]
    assert all('Source-associated event' not in t for t in texts)
    assert 'Local event review' not in caption(d)
    w=PublicWindow();qtbot.addWidget(w);w.open_recording(p);plan=w.show_plan()
    assert 'FFT' in w.plan_note.toPlainText() and not w.plan_note.toPlainText().startswith('{')
    assert w.plan_details==plan
    w.resize(1020,932);w.show();qtbot.wait(20);assert w.clock_note.height()>=w.clock_note.heightForWidth(w.clock_note.width())
    w.open_result(d);assert 'Not run' in w.evidence_view.toPlainText();assert 'Passed' not in w.evidence_view.toPlainText()
    assert 'Failed' in verification_html({'status':'rejected','reason':'/Users/private/example'})
    w.report_error('Traceback\nFileNotFoundError: /Users/private/input.h5');assert '/Users/' not in w.status.text() and '/Users/' in w.last_error
    assert not w.presentation.model().item(w.presentation.findData('aligned')).isEnabled()
