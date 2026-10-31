from pathlib import Path
from dataclasses import replace
import os
import shutil
import numpy as np
import pytest
import h5py
from scipy.signal import welch
from das_workbench.contracts import Selection,AnalysisRecipe,samples,utc_us,iso,read_json
from das_workbench.adapter import read_metadata,group_runs,RunReader,TimingError,SourceChangedError,validate_time_and_status
from das_workbench.fixtures import write_fixture,signal_fixture
from das_workbench.project import Project
from das_workbench.engine import create_job,run_job,estimate


def setup_run(tmp_path,rate=200,channels=7,seconds=24,flag=False):
    x = signal_fixture(rate,channels,seconds)
    sources = []
    for i in range(3):
        a,b = i*x.shape[1]//3,(i+1)*x.shape[1]//3
        gp = np.zeros(b-a,dtype=np.uint8)
        if flag and i == 1:
            gp[10] = 2
        p = write_fixture(tmp_path/f"part{i}.h5",x[:,a:b],rate,
                          first_us=1635984002025000+a*(1_000_000//rate),start_index=a,counter_start=a,gp=gp)
        sources.append(read_metadata(p,"root",tmp_path))
    runs,conflicts = group_runs(sources)
    assert len(runs)==1 and not conflicts
    return x,runs[0],{"root":str(tmp_path)}


def test_half_up_exact_selection_and_units(tmp_path):
    assert samples("0.0025","200") == 1
    x,run,roots = setup_run(tmp_path)
    sel = Selection(run.start_us+1,run.start_us+10001,(0,1)).resolve(run)
    assert (sel["start_sample"],sel["stop_sample"]) == (1,3)
    assert iso(utc_us("2021-11-04T00:00:02.025000Z")) == "2021-11-04T00:00:02.025000Z"
    with pytest.raises(ValueError): AnalysisRecipe(quantity="SPL").resolve(run.first)
    with pytest.raises(ValueError): AnalysisRecipe(bands=((1,101),)).resolve(run.first)
    with pytest.raises(ValueError): AnalysisRecipe(bands=((.01,.02),)).resolve(run.first)
    with pytest.raises(ValueError): AnalysisRecipe(nfft=799).resolve(run.first)
    r = AnalysisRecipe(bands=((0,100),)).resolve(run.first)
    assert r["bands"][0]["last_bin"] == 400
    assert r["df"] == .25 and r["enbw_hz"] == pytest.approx(.375)


@pytest.mark.parametrize("rate",[125,200,1000])
def test_cross_file_engine_and_resume(tmp_path,rate):
    x,run,roots = setup_run(tmp_path/"sources",rate)
    selection = Selection(run.start_us,run.end_us,(2,6,0))
    recipe = AnalysisRecipe(quantity="counts",bands=((0,rate/2),(5,15)))
    for name in ("whole","resumed"):
        create_job(tmp_path/name,run,roots,selection,recipe)
    assert run_job(tmp_path/"resumed",stop_after_units=2)["state"] == "paused"
    assert run_job(tmp_path/"resumed")["state"] == "completed"
    run_job(tmp_path/"whole")
    with h5py.File(tmp_path/"whole/result.h5") as a,h5py.File(tmp_path/"resumed/result.h5") as b:
        for path in ["welch/psd","stft/psd","rms/value","stft/center_us_x2","welch/valid","stft/band_power"]:
            np.testing.assert_array_equal(a[path][:],b[path][:])
        _,ref = welch(x[[2,6,0]].astype(float),fs=rate,window="hann_periodic",nperseg=rate*4,noverlap=rate*2)
        np.testing.assert_allclose(a["welch/psd"][:],ref,rtol=1e-10,atol=1e-12)
        expected = np.sqrt(np.mean(x[[2,6,0]].astype(float).reshape(3,-1,rate)**2,axis=-1))
        np.testing.assert_allclose(a["rms/value"][:],expected,rtol=1e-12)
        centers = a["stft/center_us_x2"][:]
        assert centers[0] == 2*run.start_us+(rate*4-1)*(1_000_000//rate)
        fullband = a["stft/band_power"][:,:,0]
        np.testing.assert_allclose(fullband,a["stft/psd"][:].sum(axis=-1)*.25,rtol=1e-12)


def test_mask_keeps_window_grid_ac_reference_and_checkpoint_repair(tmp_path):
    x,run,roots = setup_run(tmp_path/"sources",flag=True)
    s = Selection(run.start_us,run.end_us,(1,6))
    recipe = AnalysisRecipe(quantity="counts",ac_rms=True)
    output = tmp_path/"job"
    create_job(output,run,roots,s,recipe)
    run_job(output,stop_after_units=1)
    unit = next((output/"units").glob("*.h5"))
    unit.write_bytes(b"interrupted output")
    run_job(output)
    with h5py.File(output/"result.h5") as f:
        valid = f["stft/valid"][0]
        starts = f["stft/start_sample"][:]
        bad = x.shape[1]//3+10
        expected = ~((starts<=bad)&(starts+800>bad))
        np.testing.assert_array_equal(valid,expected)
        assert np.isnan(f["stft/psd"][0,~valid,:]).all()
        assert np.nanmax(f["rms/value"][0]) == 0
        mask = np.ones(x.shape[1],dtype=bool);mask[bad]=False
        mean = x[6,mask].astype(float).mean()
        y = x[6].astype(float)-mean
        rms = np.sqrt(np.mean(y.reshape(-1,200)**2,axis=-1));rms[bad//200]=np.nan
        np.testing.assert_allclose(f["rms/value"][1],rms,atol=1e-7)
    # No duplicate window contribution after repairing a corrupt checkpoint.
    assert read_json(output/"statistics.json")[0]["coverage"]["stft"]["excluded_windows"] == int((~expected).sum())


def test_gaps_overlaps_configuration_and_sidecars(tmp_path):
    x,run,roots = setup_run(tmp_path)
    a,b,c = run.files
    runs,conf = group_runs([a,replace(b,first_us=b.first_us+5000,last_us=b.last_us+5000),c])
    assert len(runs)>1
    runs,conf = group_runs([a,a,b,c])
    assert conf[0]["reason"] == "overlap or duplicate coverage"
    runs,conf = group_runs([a,replace(b,config_id="other"),c])
    assert len(runs)==3
    (tmp_path/"._part0.h5").write_bytes(b"AppleDouble")
    (tmp_path/"broken.h5").write_bytes(b"not HDF5")
    with Project(tmp_path/"project") as p:
        report = p.scan([tmp_path],deep=True)
        assert report["files"] == 3 and report["ignored_sidecars"] == 1 and report["errors"]


def test_axis_mapping_unknown_status_source_change_and_relink(tmp_path):
    data = signal_fixture(125,7,12)
    p = write_fixture(tmp_path/"source/a.h5",data,125,transpose=True,missing_status=True)
    s = read_metadata(p,"r",p.parent)
    run = group_runs([s])[0][0]
    reader = RunReader(run,{"r":str(p.parent)})
    arr,valid,reasons = reader.read([6,1],10,50)
    np.testing.assert_array_equal(arr,data[[6,1],10:50])
    assert valid.all() and np.all(reasons & 32)
    reader = RunReader(run,{"r":str(p.parent)},strict=True)
    assert not reader.read([0],0,10)[1].any()
    with Project(tmp_path/"project") as project:
        project.scan([p.parent])
        root = next(iter(project.roots))
        shutil.copytree(p.parent,tmp_path/"moved")
        project.relink(root,tmp_path/"moved")
        assert project.roots[root] == str(tmp_path/"moved")
    os.utime(p,ns=(p.stat().st_atime_ns,p.stat().st_mtime_ns+1_000_000))
    with pytest.raises(SourceChangedError): reader.read([0],0,10)


def test_timing_discontinuity_and_wrong_axes(tmp_path):
    data = signal_fixture(200,7,12)
    p = write_fixture(tmp_path/"a.h5",data,200)
    with h5py.File(p,"r+") as f:
        f["/Acquisition/Raw[0]/RawDataTime"][100] += 1
    s = read_metadata(p,"r",tmp_path)
    with pytest.raises(TimingError): validate_time_and_status(s,{"r":str(tmp_path)})
    p = write_fixture(tmp_path/"b.h5",data,200,axis_override=["x","y"])
    with pytest.raises(ValueError,match="Ambiguous axes"):read_metadata(p,"r",tmp_path)


def test_cancel_missing_source_and_immutability(tmp_path):
    x,run,roots = setup_run(tmp_path/"source")
    s = Selection(run.start_us,run.end_us,(0,2))
    create_job(tmp_path/"job",run,roots,s,AnalysisRecipe())
    assert run_job(tmp_path/"job",control=lambda:"cancel")["state"] == "cancelled"
    next((tmp_path/"source").glob("*.h5")).unlink()
    with pytest.raises(FileNotFoundError):run_job(tmp_path/"job")
    assert read_json(tmp_path/"job/job.json")["state"] == "failed"
