"""Publication-review regressions, including ordinary Qt paths without presets."""
import json
import numpy as np
import h5py,pytest
from das_workbench import native
from das_workbench.native import open_recording
from das_workbench.public_engine import plan
from das_workbench.public_ui import PublicWindow
from das_workbench.public_selection import review_scope,qualify_review,interval_indices,coordinate_indices
from test_public_native import asn,recipe


def pressure_fixture(path,unit='Pa',signal='Sound Pressure',scale=3.5400390625):
    with h5py.File(path,'w') as f:
        d=f.create_dataset('Recorder-C',data=np.ones((100,1)))
        for k,v in {'Sample Rate (Hz)':100.,'Start Time':'2022-01-01T00:00:00+00:00','End Time':'2022-01-01T00:00:01+00:00','Signal':signal}.items():d.attrs[k]=v
        if unit is not None:d.attrs['Unit']=unit
        d=f.create_dataset('DAS',data=np.ones((5000,1),dtype='i2'))
        for k,v in dict(OutputDataRate=5000.,PartStartTime='2022-01-01T00:00:00+00:00',PartEndTime='2022-01-01T00:00:01+00:00',RawDataUnit='(nm/m)/s',AmpScaling=scale,GaugeLength=10.).items():d.attrs[k]=v


@pytest.mark.parametrize('unit,signal,verified,eligible',[('Pa','Sound Pressure',True,True),('V','Voltage',True,False),(None,'Sound Pressure',True,False),('Pa','Voltage',True,False),('V','Sound Pressure',True,False),('Pa','Sound Pressure',False,False)])
def test_pressure_eligibility_requires_values_and_provenance(tmp_path,monkeypatch,unit,signal,verified,eligible):
    p=tmp_path/'x.h5';pressure_fixture(p,unit,signal)
    if verified:
        original=native.identity
        monkeypatch.setattr(native,'identity',lambda p:dict(original(p),sha256=native.LAKE_PUBLICATION_SHA256))
    signals=open_recording(p);s=next(s for s in signals if s.group=='/Recorder-C');assert s.pressure is eligible
    r=recipe(s);r['signals'][0].update(start=0,end=100,channels=[0],trace=0,window_s=.1,hop_s=.05,nfft=10,fmax=50,pressure_window_s=.1)
    if eligible:assert plan(signals,r)['allowed']
    else:
        with pytest.raises(ValueError,match='verified pressure'):plan(signals,r)


@pytest.mark.parametrize('verified,scale',[(False,3.5400390625),(True,7.)])
def test_scaling_provenance_not_inherited(tmp_path,monkeypatch,verified,scale):
    p=tmp_path/'x.h5';pressure_fixture(p,scale=scale)
    if verified:
        original=native.identity;monkeypatch.setattr(native,'identity',lambda p:dict(original(p),sha256=native.LAKE_PUBLICATION_SHA256))
    s=open_recording(p)[0];assert s.group=='/DAS' and s.quantity=='Unqualified native amplitude';assert not s.pressure
    assert np.all(s.read(0,10,[0])==1)


def test_normal_qt_open_nyquist_and_time_coordinate_controls(tmp_path,qtbot):
    p=tmp_path/'asn.h5';asn(p);w=PublicWindow();qtbot.addWidget(w);w.open_recording(p)
    assert w.show_plan()['allowed'];assert w.fmax.value()<=w.signals[0].fs/2
    w.seconds.setValue(.55);w.duration.setValue(1.1);w.apply_seconds();assert (w.start.value(),w.end.value())==(40,120)
    w.coord_low.setValue(4);w.coord_high.setValue(12.3);w.coord_trace.setValue(8.2);w.apply_coordinates();assert w.indices()==[1,2,3] and w.trace.value()==2
    assert 'Unreviewed' in w.recipe()['event_review']


def test_review_bound_to_source_selection_and_coordinate(tmp_path,qtbot):
    p=tmp_path/'asn.h5';asn(p);s=open_recording(p)[0];s.source['sha256']='a'*64;r=recipe(s);r['event_review']='Reviewed event';r['event_review_scope']=review_scope(s.source,r['signals'])
    assert qualify_review(r,s.source)['event_review']=='Reviewed event'
    for change in ['start','end','trace','coordinate','channels']:
        changed=json.loads(json.dumps(r));x=changed['signals'][0]
        if change=='channels':x[change]=[1,2,3]
        elif change=='coordinate':x[change]='other origin'
        else:x[change]+=1
        assert qualify_review(changed,s.source)['event_review_status']=='unreviewed selection'
    assert qualify_review(r,dict(s.source,sha256='b'*64))['event_review_status']=='unreviewed selection'
    w=PublicWindow();qtbot.addWidget(w);w.open_recording(p);w.signals[0].source['sha256']='a'*64;w.apply_recipe(r|{'subtitle':'Event-specific subtitle'})
    assert w.recipe()['event_review']=='Reviewed event';w.start.setValue(w.start.value()+1);out=w.recipe();assert out['event_review_status']=='unreviewed selection' and out['title']=='Native recording | unreviewed selection'


def test_enbw_tiny_periodic_hann_and_half_open_boundaries(tmp_path):
    p=tmp_path/'asn.h5';asn(p);s=open_recording(p)[0];r=recipe(s);r['signals'][0].update(window_s=2*s.dt,hop_s=s.dt,nfft=2)
    assert plan([s],r)['signals'][0]['enbw_hz']==s.fs
    assert interval_indices(s,40*s.dt,80*s.dt)==(40,120)
    with pytest.raises(ValueError):interval_indices(s,s.n*s.dt,1)


def test_widget_resolution_preserves_native_bounds_and_resets_title(tmp_path,qtbot):
    p=tmp_path/'asn.h5';asn(p);w=PublicWindow();qtbot.addWidget(w);w.open_recording(p)
    s=w.signals[0];s.dt=.001759922562478001
    w.end.setValue(s.n);w.sync_time_fields();w.apply_seconds();assert w.end.value()==s.n
    w.title.setText('Old interpreted event');w.open_recording(p);assert w.title.text()=='Native recording · selected section'
    v=s.coordinates[next(iter(s.coordinates))];ch,tr=coordinate_indices(s,next(iter(s.coordinates)),round(float(v[0]),6),round(float(v[-1]),6),float(v[1]),resolution=1e-6)
    assert ch==list(range(s.c))
