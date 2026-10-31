"""Independent native-array answers, quality/boundary failures, UI and export."""
from pathlib import Path
from dataclasses import replace
import json
import subprocess
import sys
import numpy as np
import h5py
import pytest
from das_workbench.adapter import group_runs, read_metadata, TimingError
from das_workbench.contracts import read_json, file_hash
from das_workbench.fixtures import signal_fixture, write_fixture, make_demo
from das_workbench.overview import compute_overview, load_overview, export_overview, resolve_overview
from das_workbench.project import Project
from das_workbench.ui import MainWindow, OverviewDialog


def sources(tmp_path, rate=200, flags=False, transpose=False):
    # Two files; boundary and selected start are deliberately off display bins.
    x=signal_fixture(rate,11,3)
    files=[]
    split=rate+3
    for i,(a,b) in enumerate(((0,split),(split,x.shape[1]))):
        gp=np.zeros(b-a,dtype=np.uint8)
        if flags:gp[7]=2
        p=write_fixture(tmp_path/f"part{i}.h5",x[:,a:b],rate,
            first_us=1635984002025000+a*(1_000_000//rate),start_index=a,counter_start=a,gp=gp,transpose=transpose)
        files.append(read_metadata(p,"root",tmp_path))
    return x,group_runs(files)[0][0],{"root":str(tmp_path)},split


@pytest.mark.parametrize("rate,transpose",[(200,False),(1000,True)])
def test_native_answers_and_display_independence(tmp_path,rate,transpose):
    x,run,roots,split=sources(tmp_path/"src",rate,True,transpose)
    start,stop=5,x.shape[1]-7
    valid=np.ones(stop-start,dtype=bool)
    valid[np.array([7,split+7])-start]=False
    # Shift integer data before independent NumPy population std; includes large DC.
    values=x[:,start:stop].astype(np.float64)-x[:,start:start+1]
    reference_mean=np.mean(values[:,valid],axis=1)
    scale=run.first.phase_scale
    for name,stride,bin_n,block in [("fine",1,1,71),("reduced",3,13,97)]:
        directory=tmp_path/name
        compute_overview(directory,run,roots,run.start_us+start*run.first.tick_us,
            run.start_us+stop*run.first.tick_us,dict(trace_channel=6,channel_stride=stride,time_bin_samples=bin_n),
            channel_block=4,time_block=block)
        m,d=load_overview(directory)
        np.testing.assert_allclose(d["rms_rad"],np.std(values[:,valid],axis=1)*scale,atol=2e-12,rtol=1e-12)
        reference=(values-reference_mean[:,None])*scale
        reference[:,~valid]=np.nan
        np.testing.assert_allclose(d["trace/phase_rad"],reference[6],atol=2e-12)
        expected=np.stack([np.mean(reference[::stride,a:a+bin_n],axis=1) for a in range(0,stop-start,bin_n)],axis=1)
        np.testing.assert_allclose(d["waterfall/phase_rad"],expected,atol=2e-12)
        assert m["quality"]["excluded_samples"]==2
        assert d["waterfall/time_edges_s"][-1]==(stop-start)/rate
        assert d["rms_rad"][0]==d["rms_rad"][1]==0
        assert len(d["rms_rad"])==11
    _,fine=load_overview(tmp_path/"fine");_,coarse=load_overview(tmp_path/"reduced")
    np.testing.assert_allclose(fine["rms_rad"],coarse["rms_rad"],atol=2e-12)
    np.testing.assert_allclose(fine["trace/phase_rad"],coarse["trace/phase_rad"],atol=2e-12)


def test_known_sinusoid_and_exact_zero(tmp_path):
    rate=200;t=np.arange(1000)/rate
    # Exact discrete four-sample sinusoid amplitude 100 counts; known RMS 100/sqrt(2).
    x=np.stack([np.zeros(1000),np.full(1000,2_000_000_000),np.tile([0,100,0,-100],250)]).astype(np.int32)
    p=write_fixture(tmp_path/"src/a.h5",x,rate)
    s=read_metadata(p,"r",p.parent);run=group_runs([s])[0][0]
    compute_overview(tmp_path/"out",run,{"r":str(p.parent)},run.start_us,run.end_us,
                     dict(channel_stride=1,time_bin_samples=1,trace_channel=2),time_block=123)
    m,d=load_overview(tmp_path/"out")
    np.testing.assert_allclose(d["rms_rad"],[0,0,100/np.sqrt(2)*s.phase_scale],rtol=1e-14)
    np.testing.assert_array_equal(d["trace/phase_rad"],x[2]*s.phase_scale)


def test_cancel_no_partial_output_and_bad_inputs(tmp_path):
    x,run,roots,_=sources(tmp_path/"src")
    with pytest.raises(InterruptedError):
        compute_overview(tmp_path/"cancel",run,roots,run.start_us,run.end_us,control=lambda:"cancel")
    assert not (tmp_path/"cancel").exists()
    assert not list(tmp_path.glob(".overview-*"))
    for settings in (dict(trace_channel=11),dict(channel_stride=0),dict(time_bin_samples=10000),
                     dict(colour_limit=-2),dict(clip_percentile=float("nan"))):
        with pytest.raises(ValueError):resolve_overview(run,run.start_us,run.end_us,settings)
    missing=replace(run,files=tuple(replace(s,spacing_m=None) for s in run.files))
    with pytest.raises(ValueError,match="spacing"):resolve_overview(missing,run.start_us,run.end_us)
    with h5py.File(tmp_path/"src/part0.h5","r+") as f:f["Acquisition/Raw[0]/RawDataTime"][20]+=1
    updated=group_runs([read_metadata(tmp_path/"src/part0.h5","root",tmp_path/"src"),run.files[1]])[0][0]
    with pytest.raises(TimingError):compute_overview(tmp_path/"badtime",updated,roots,updated.start_us,updated.end_us)
    assert not (tmp_path/"badtime").exists()


def test_strict_quality_and_all_display_bins_missing(tmp_path):
    x=signal_fixture(200,3,2);gp=np.zeros(400,dtype=np.uint8);gp[::10]=1
    p=write_fixture(tmp_path/"src/a.h5",x,200,gp=gp)
    s=read_metadata(p,"r",p.parent);run=group_runs([s])[0][0];roots={"r":str(p.parent)}
    for strict in (False,True):
        dest=tmp_path/str(strict)
        compute_overview(dest,run,roots,run.start_us,run.end_us,dict(strict_quality=strict,time_bin_samples=10,channel_stride=1))
        m,d=load_overview(dest)
        assert m["quality"]["excluded_samples"]==(40 if strict else 0)
        if strict:
            assert np.isnan(d["waterfall/phase_rad"]).all()
            assert m["colour"]["finite_pixels"]==0
    p=write_fixture(tmp_path/"bad/a.h5",x,200,gp=np.full(400,2,dtype=np.uint8))
    s=read_metadata(p,"r",p.parent);run=group_runs([s])[0][0]
    with pytest.raises(ValueError,match="No valid samples"):
        compute_overview(tmp_path/"all_bad",run,{"r":str(p.parent)},run.start_us,run.end_us)


def test_portable_bundle_replay_and_checksum(tmp_path):
    x,run,roots,_=sources(tmp_path/"src")
    dest=tmp_path/"result"
    compute_overview(dest,run,roots,run.start_us,run.end_us,dict(trace_channel=6))
    export_overview(dest,tmp_path/"bundle",dict(dpi=90))
    for name in ("overview.png","overview.pdf","overview.svg","overview.h5","spatial_rms.csv","caption.txt","overview_recipe.json"):
        assert (tmp_path/"bundle"/name).stat().st_size>0
    checks=read_json(tmp_path/"bundle/bundle_checksums.json")
    assert all(file_hash(tmp_path/"bundle"/name)==checksum for name,checksum in checks.items())
    assert not (tmp_path/"bundle/local_roots.json").exists()
    assert str(tmp_path) not in (tmp_path/"bundle/overview_manifest.json").read_text()
    rootmap=tmp_path/"roots.json";rootmap.write_text(json.dumps(roots))
    subprocess.run([sys.executable,"-m","das_workbench","overview-replay",str(tmp_path/"bundle"),
        "--output",str(tmp_path/"replay"),"--roots",str(rootmap)],check=True,timeout=60)
    _,a=load_overview(dest);_,b=load_overview(tmp_path/"replay")
    for key in a:np.testing.assert_array_equal(a[key],b[key])
    with pytest.raises(FileExistsError):export_overview(dest,tmp_path/"bundle")
    with h5py.File(dest/"overview.h5","r+") as f:f["rms_rad"][0]=100
    with pytest.raises(ValueError,match="checksum"):load_overview(dest)


def test_gui_generate_reopen_export(qtbot,tmp_path):
    make_demo(tmp_path/"source")
    with Project(tmp_path/"project") as p:p.scan([tmp_path/"source"])
    w=MainWindow(str(tmp_path/"project"));qtbot.addWidget(w);w.show()
    dialog=OverviewDialog(w.current_run(),w.current_run().start_us,w.current_run().end_us)
    assert "native samples" in dialog.info.text()
    w.channels.setText("2") # The dedicated overview must still include every channel.
    w.generate_overview(dict(trace_channel=6,channel_stride=2,time_bin_samples=10))
    qtbot.waitUntil(lambda:w.overview_directory is not None and w.process is None,timeout=60000)
    assert w.tabs.currentWidget()==w.fibre_tab
    m=read_json(Path(w.overview_directory)/"overview_manifest.json")
    assert m["estimate"]["all_channels"]==32
    assert w.fibre_export.isEnabled()
    assert w.jobs.item(0).text().startswith("Overview")
    w.open_saved_job(w.jobs.item(0))
    w.start_worker(dict(task="overview_export",directory=w.overview_directory,destination=str(tmp_path/"export"),settings=dict(dpi=90)))
    qtbot.waitUntil(lambda:w.process is None,timeout=60000)
    assert (tmp_path/"export/overview.pdf").exists()
    w.close()
