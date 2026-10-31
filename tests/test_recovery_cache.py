from pathlib import Path
import shutil
from unittest.mock import patch
import numpy as np
import pytest
from das_workbench.cache import DisplayCache
from das_workbench.contracts import Selection,AnalysisRecipe,read_json
from das_workbench.engine import create_job,run_job
from das_workbench.fixtures import make_demo
from das_workbench.project import Project


def new_job(tmp_path):
    make_demo(tmp_path/"source")
    with Project(tmp_path/"project") as p:
        p.scan([tmp_path/"source"]);run=p.runs()[0][0];roots=p.roots
    create_job(tmp_path/"job",run,roots,Selection(run.start_us,run.end_us,(2,6)),AnalysisRecipe())
    return tmp_path/"job"


def test_disk_full_preflight_and_unit_failure_recover(tmp_path):
    job=new_job(tmp_path)
    with patch("das_workbench.engine.shutil.disk_usage",return_value=shutil._ntuple_diskusage(100,100,0)):
        with pytest.raises(OSError,match="Insufficient"):run_job(job)
    run_job(job,stop_after_units=1)
    preserved={p.name:p.read_bytes() for p in (job/"units").glob("*.h5")}
    with patch("das_workbench.engine._write_unit",side_effect=OSError("injected output-space exhaustion")):
        with pytest.raises(OSError):run_job(job)
    assert read_json(job/"job.json")["state"]=="failed"
    for name,values in preserved.items():assert (job/"units"/name).read_bytes()==values
    assert run_job(job)["state"]=="completed"


def test_unreadable_waveform_preserves_units(tmp_path):
    job=new_job(tmp_path)
    run_job(job,stop_after_units=1)
    with patch("das_workbench.engine.RunReader.read",side_effect=OSError("injected compressed chunk decode error")):
        with pytest.raises(OSError,match="chunk"):run_job(job)
    assert len(list((job/"units").glob("*.h5")))==1
    assert run_job(job)["state"]=="completed"


def test_display_cache_quota_and_corruption(tmp_path):
    cache=DisplayCache(tmp_path,quota_bytes=3000)
    rng=np.random.default_rng(2)
    for i in range(5):cache.put(str(i),dict(data=rng.normal(size=100),description="display only"))
    assert sum(p.stat().st_size for p in tmp_path.iterdir())<=3000
    latest=cache.get("4")
    assert latest["display_cache_hit"]
    (tmp_path/"4.npz").write_bytes(b"broken")
    assert cache.get("4") is None


def test_actual_damaged_compressed_fixture_chunk(tmp_path):
    import h5py
    from das_workbench.fixtures import write_fixture,signal_fixture
    from das_workbench.adapter import read_metadata,group_runs,RunReader
    p=write_fixture(tmp_path/"damaged.h5",signal_fixture(200,7,12),200)
    with h5py.File(p,"r") as f:
        info=f["/Acquisition/Raw[0]/RawData"].id.get_chunk_info_by_coord((0,0))
        offset=info.byte_offset
    # This is a disposable generated fixture, never a source recording.
    with p.open("r+b") as f:f.seek(offset);f.write(b"\xff"*16)
    source=read_metadata(p,"r",tmp_path)
    run=group_runs([source])[0][0]
    with pytest.raises(OSError):RunReader(run,{"r":str(tmp_path)}).read([0],0,100)
