"""v0.2.1 acceptance regressions; scientific thresholds are not relaxed."""
from pathlib import Path
from dataclasses import replace
import json
import multiprocessing as mp
import shutil
import time
import numpy as np
import h5py
import pytest
from das_workbench.adapter import read_metadata,group_runs,SourceChangedError
from das_workbench.contracts import read_json,atomic_json,file_hash,jsonable
from das_workbench.fixtures import signal_fixture,write_fixture,make_demo
from das_workbench.overview import plan_overview,compute_overview,load_overview,export_overview,make_overview_figure
from das_workbench.overview_jobs import run_overview_job,record_path,reconcile,is_legacy_overview
from das_workbench.overview_evidence import attach_report,evidence_status
from das_workbench.ui import MainWindow,OverviewDialog
from das_workbench.project import Project
from das_workbench.worker import worker_main
from validation.reference_overview import validate


def source(tmp_path,rate=125,channels=11,n=1257):
    x=signal_fixture(rate,channels,n/rate)
    p=write_fixture(tmp_path/'data/a.h5',x,rate)
    s=read_metadata(p,'r',p.parent)
    return group_runs([s])[0][0],{'r':str(p.parent)}


def fake_run(channels,n,rate=1000):
    # Metadata-only fixture, deliberately no accessible waveform path.
    from das_workbench.contracts import SourceFile,ContinuousRun
    tick=1_000_000//rate
    s=SourceFile('x','r','absent.h5',0,0,'a','a',str(rate),tick,channels,n,0,(n-1)*tick,0,0,n-1,1,2.,0,30.,2*np.pi/65536,['locus','time'],{})
    return ContinuousRun('x',(s,))


@pytest.mark.parametrize('rate,maxn',[(200,39250),(1000,39250)])
def test_planner_fibre_boundary_and_explicit_accept_decline(qtbot,rate,maxn):
    run=fake_run(32600,maxn,rate)
    _,_,e=plan_overview(run,run.start_us,run.end_us)
    assert e['allowed'] and e['waterfall_rows']==2038 and e['waterfall_columns']==3925
    run=fake_run(32600,maxn+1,rate)
    _,_,e=plan_overview(run,run.start_us,run.end_us)
    assert not e['allowed'] and e['waterfall_columns']==3926 and e['final_bin_samples']==1
    d=OverviewDialog(run,run.start_us,run.end_us);qtbot.addWidget(d)
    requested=d.settings();assert d.suggest.isEnabled()
    d.reject();assert d.settings()==requested and d.planning_decision is None
    d.accept_suggested_bin()
    assert d.settings()['time_bin_samples']==11 and d.settings()['channel_stride']==16
    assert d.planning_decision['requested']==requested
    assert d.buttons.button(d.buttons.StandardButton.Ok).isEnabled()


def test_planner_exact_caps_and_trace_cannot_coarsen():
    # One display row, bin one gives exact 8M-pixel equality.
    r=fake_run(1,8_000_000)
    assert plan_overview(r,0,r.end_us,dict(time_bin_samples=1))[2]['allowed']
    r=fake_run(1,8_000_001)
    assert not plan_overview(r,0,r.end_us,dict(time_bin_samples=1))[2]['allowed']
    r=fake_run(1,10_000_000)
    assert plan_overview(r,0,r.end_us)[2]['allowed']
    r=fake_run(1,10_000_001)
    e=plan_overview(r,0,r.end_us,dict(time_bin_samples=100))[2]
    assert not e['allowed'] and e['suggested_bin_samples'] is None
    assert 'coarsening cannot fix' in ' '.join(e['blocking_reasons'])


def test_independent_125hz_strict_gate_and_native_display_invariance(tmp_path):
    r,roots=source(tmp_path)
    for name,bin_n,stride in [('fine',1,1),('coarse',17,3)]:
        compute_overview(tmp_path/name,r,roots,r.start_us,r.end_us,dict(trace_channel=6,time_bin_samples=bin_n,channel_stride=stride),channel_block=4,time_block=137)
        report=validate(tmp_path/name,roots)
        assert report['status']=='passed'
    _,a=load_overview(tmp_path/'fine');_,b=load_overview(tmp_path/'coarse')
    for key in ('rms_rad','channel_mean_rad','trace/phase_rad'):
        # Block alignment may change reduction order, bounded by the fixed gate.
        tau=abs(r.first.phase_scale)*1e-6+1e-10*a['rms_rad']
        if key.startswith('trace'):tau=tau[6]
        assert np.all(np.abs(a[key]-b[key])<=tau)


def test_evidence_binding_absent_mutations_and_offline_reexport(tmp_path):
    r,roots=source(tmp_path)
    original=tmp_path/'original'
    compute_overview(original,r,roots,r.start_us,r.end_us,dict(trace_channel=6))
    assert evidence_status(original)['status']=='not supplied'
    report=attach_report(original,validate(original,roots));assert evidence_status(original)['status']=='passed'
    before={p.name:file_hash(p) for p in original.iterdir() if p.is_file()}
    # Offline export: source moved, neither load nor export may read source.
    shutil.move(tmp_path/'data',tmp_path/'offline')
    export_overview(original,tmp_path/'export',dict(dpi=72))
    assert evidence_status(tmp_path/'export')['status']=='passed'
    assert before=={p.name:file_hash(p) for p in original.iterdir() if p.is_file()}
    for kind in ('numeric','recipe','selection','sidecar','report_status'):
        dest=tmp_path/kind;shutil.copytree(original,dest)
        if kind=='numeric':
            with h5py.File(dest/'overview.h5','r+') as f:f['rms_rad'][2]+=1
        elif kind=='recipe':
            data=read_json(dest/'overview_recipe.json');data['settings']['trace_channel']=3;atomic_json(dest/'overview_recipe.json',data)
        elif kind=='selection':
            data=read_json(dest/'overview_manifest.json');data['selection']['first_us']+=1;atomic_json(dest/'overview_manifest.json',data)
        elif kind=='report_status':
            data=read_json(dest/'validation_report.json');data['checks']['mean']['passed']=False;atomic_json(dest/'validation_report.json',data)
            m=read_json(dest/'overview_manifest.json');m['validation_report']['sha256']=file_hash(dest/'validation_report.json');atomic_json(dest/'overview_manifest.json',m)
        else:(dest/'validation_report.json').write_text('{}')
        assert evidence_status(dest)['status']=='rejected'
    moved={'r':str(tmp_path/'offline')}
    compute_overview(tmp_path/'relocated',r,moved,r.start_us,r.end_us,dict(trace_channel=6))
    _,a=load_overview(original);_,b=load_overview(tmp_path/'relocated')
    for key in a:np.testing.assert_array_equal(a[key],b[key])
    with h5py.File(tmp_path/'offline/a.h5','r+') as f:f['Acquisition/Raw[0]/RawData'][0,0]+=1
    with pytest.raises(SourceChangedError):compute_overview(tmp_path/'changed',r,moved,r.start_us,r.end_us)


@pytest.mark.parametrize('stage',['start','middle','prepublication'])
def test_stop_discards_only_current_job(tmp_path,stage):
    r,roots=source(tmp_path)
    prior=tmp_path/'overviews/prior';run_overview_job(prior,r,roots,r.start_us,r.end_us)
    checksum=file_hash(prior/'overview.h5');stop=[stage=='start']
    def progress(done,total,label):
        if stage=='middle' and done>=1:stop[0]=True
        if stage=='prepublication' and label=='Ready to publish overview':stop[0]=True
    new=tmp_path/'overviews/stopped'
    with pytest.raises(InterruptedError):
        run_overview_job(new,r,roots,r.start_us,r.end_us,progress=progress,control=lambda:'cancel' if stop[0] else None,channel_block=4,time_block=100)
    assert not new.exists() and read_json(record_path(new))['state']=='interrupted'
    assert file_hash(prior/'overview.h5')==checksum
    assert not list(new.parent.glob('.overview-*'))


def test_commit_race_recovery_and_source_signature_at_publication(tmp_path,monkeypatch):
    r,roots=source(tmp_path)
    dest=tmp_path/'overviews/complete';run_overview_job(dest,r,roots,r.start_us,r.end_us)
    # Crash window after atomic rename and before separate catalogue commit.
    path=record_path(dest);record=read_json(path);record.update(state='running',pid=99999999);atomic_json(path,record)
    assert reconcile(path)['state']=='completed'
    assert is_legacy_overview(dict(state='paused',identity=dict(algorithm='full-fibre-overview-1')))
    def mutate(done,total,label):
        if label=='Ready to publish overview':
            with h5py.File(tmp_path/'data/a.h5','r+') as f:f['Acquisition/Raw[0]/RawData'][0,0]+=1
    with pytest.raises(SourceChangedError):run_overview_job(tmp_path/'changed',r,roots,r.start_us,r.end_us,progress=mutate)
    assert not (tmp_path/'changed').exists()


def test_gui_stop_ack_reopen_and_forced_worker_interruption(qtbot,tmp_path):
    make_demo(tmp_path/'data')
    with Project(tmp_path/'project') as p:p.scan([tmp_path/'data'])
    w=MainWindow(str(tmp_path/'project'));qtbot.addWidget(w);w.show()
    w.generate_overview(dict(trace_channel=6))
    dest=Path(w.current_task['directory']);begin=time.perf_counter();w.pause()
    ack=time.perf_counter()-begin
    assert ack<1 and w.status.text().startswith('Stopping overview')
    assert w.pause_button.text()=='Stop overview' and not w.cancel_button.isVisible()
    heartbeats=[]
    from PySide6.QtCore import QTimer
    timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:heartbeats.append(time.perf_counter()));timer.start()
    qtbot.waitUntil(lambda:w.process is None,timeout=30000);timer.stop()
    assert heartbeats and not dest.exists()
    assert read_json(record_path(dest))['state']=='interrupted'
    w.close()
    reopened=MainWindow(str(tmp_path/'project'));qtbot.addWidget(reopened);reopened.show()
    assert any('Interrupted — regenerate' in reopened.jobs.item(i).text() for i in range(reopened.jobs.count()))
    reopened.jobs.setCurrentRow(0);reopened.resume_selected();assert reopened.process is None
    # Force kill after owner/staging initialization by observing persisted state.
    r,roots=source(tmp_path/'large',rate=1000,channels=600,n=60000)
    with Project(tmp_path/'large_project') as p:p.scan([tmp_path/'large/data'])
    large=MainWindow(str(tmp_path/'large_project'));qtbot.addWidget(large);large.show()
    large.generate_overview(dict(trace_channel=6,time_bin_samples=10))
    killed=Path(large.current_task['directory'])
    qtbot.waitUntil(lambda:read_json(record_path(killed))['state']=='running',timeout=30000)
    large.process.terminate();large.process.join(timeout=10)
    qtbot.waitUntil(lambda:large.process is None,timeout=10000)
    assert read_json(record_path(killed))['state']=='interrupted' and not killed.exists()
    assert not list(killed.parent.glob('.overview-*'))
    large.close();reopened.close()
    recovered=MainWindow(str(tmp_path/'large_project'));qtbot.addWidget(recovered);recovered.show()
    assert recovered.process is None and any('Interrupted — regenerate' in recovered.jobs.item(i).text() for i in range(recovered.jobs.count()))
    recovered.close()
    # Keep portable measured evidence when requested by release runner.
    import os
    if os.environ.get('DAS_ACCEPTANCE_EVIDENCE'):
        atomic_json(Path(os.environ['DAS_ACCEPTANCE_EVIDENCE'])/'stop_ui_measurement.json',dict(passed=True,acknowledgement_seconds=ack,heartbeat_count=len(heartbeats),stop_completion_seconds=heartbeats[-1]-begin,forced_worker_termination=True,project_reopen=True,method='Actual Qt slot + label update measured with perf_counter; GUI QTimer serviced while spawned worker stopped; completion is separate from acknowledgement.'))


def test_zoom_export_caption_clipping_ties_zero_linear(tmp_path):
    x=np.zeros((3,125),dtype=np.int32)
    p=write_fixture(tmp_path/'data/a.h5',x,125);s=read_metadata(p,'r',p.parent);r=group_runs([s])[0][0]
    dest=tmp_path/'result';compute_overview(dest,r,{'r':str(p.parent)},r.start_us,r.end_us,dict(channel_stride=1,time_bin_samples=10))
    fig=make_overview_figure(dest);fig.axes[0].set_xlim(.2,.4);fig.axes[1].set_xlim(.3,.5)
    full=make_overview_figure(dest);assert tuple(full.axes[0].get_xlim())==(0,1)
    assert 'Per-channel phase RMS' in full.axes[2].get_title(loc='left')
    assert full.axes[2].get_yscale()=='linear'
    assert any('all-zero RMS: linear axis' in t.get_text() for t in full.texts)
    export_overview(dest,tmp_path/'export',dict(dpi=72))
    caption=(tmp_path/'export/caption.txt').read_text()
    assert caption.startswith('Vendor-processed optical phase') and 'no additional filtering or time averaging' in caption
    m=read_json(tmp_path/'export/overview_manifest.json');assert m['colour']['clipped_fraction']==0
    assert m['derived_validation_status']['status']=='not supplied'

@pytest.mark.parametrize('state',['failed','blocked','not run'])
def test_scoped_nonpassing_evidence_never_badged_passed(tmp_path,state):
    r,roots=source(tmp_path)
    dest=tmp_path/'result';compute_overview(dest,r,roots,r.start_us,r.end_us)
    report=dict(status=state,explanation='Explicit synthetic status fixture',validator={'version':'test'},method='Status binding regression',environment={'fixture':True},thresholds={'not_evaluated':True},checks={})
    attach_report(dest,report)
    assert evidence_status(dest)==dict(status=state,binding_valid=True,explanation='Explicit synthetic status fixture')

def test_stop_racing_atomic_commit_retains_complete_artifact(tmp_path,monkeypatch):
    import das_workbench.overview as overview
    r,roots=source(tmp_path);dest=tmp_path/'overviews/race';stop=[False]
    original=overview.os.replace
    def rename(src,target):
        if Path(target)==dest:stop[0]=True
        return original(src,target)
    monkeypatch.setattr(overview.os,'replace',rename)
    run_overview_job(dest,r,roots,r.start_us,r.end_us,control=lambda:'cancel' if stop[0] else None)
    assert stop[0] and read_json(record_path(dest))['state']=='completed'
    load_overview(dest)


def test_legacy_paused_overview_without_selection_does_not_resume(qtbot,tmp_path):
    with Project(tmp_path/'project'):pass
    path=tmp_path/'project/jobs/legacy/job.json'
    atomic_json(path,dict(task='overview',state='paused',algorithm='full-fibre-overview-1'))
    w=MainWindow(str(tmp_path/'project'));qtbot.addWidget(w);w.show()
    assert w.jobs.count()==1 and 'Interrupted — regenerate' in w.jobs.item(0).text()
    w.jobs.setCurrentRow(0);w.resume_selected();assert w.process is None and w.pending==[]
    assert read_json(path)['state']=='paused' # Historical record remains unmodified.
    w.close()
