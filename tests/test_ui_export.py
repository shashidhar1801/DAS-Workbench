import os
import json
import subprocess
import sys
from pathlib import Path
import numpy as np
import h5py
from PySide6 import QtCore
from das_workbench.ui import MainWindow
from das_workbench.selection import parse_channels
from das_workbench.project import Project
from das_workbench.fixtures import make_demo
from das_workbench.engine import create_job,run_job
from das_workbench.contracts import Selection,AnalysisRecipe,read_json
from das_workbench.exporter import export_bundle,result_view


def test_ui_shared_worker_and_display_independence(qtbot,tmp_path):
    sources=tmp_path/"sources";make_demo(sources)
    window=MainWindow(str(tmp_path/"project"));qtbot.addWidget(window);window.show()
    window.scan([str(sources)],deep=True)
    qtbot.waitUntil(lambda:window.process is None,timeout=30000)
    assert len(window.runs)==1
    window.channels.setText("2,6")
    window.analyse()
    qtbot.waitUntil(lambda:window.result is not None and window.process is None,timeout=60000)
    result=window.result_directory
    with h5py.File(Path(result)/"result.h5") as f:before=f["welch/psd"][:]
    window.resize(1000,700);window.log_frequency.setChecked(True);window.psd.setXRange(1,80)
    with h5py.File(Path(result)/"result.h5") as f:np.testing.assert_array_equal(before,f["welch/psd"][:])
    window.window.setText("8")
    assert "Controls edited" in window.snapshot.text()
    assert window.result["manifest"]["recipe"]["window"] == 800
    window.preview_selection()
    qtbot.waitUntil(lambda:window.preview is not None and window.process is None,timeout=30000)
    origin=window.preview["selection"]["first_us"]
    window.selection_region.setRegion((.000001,.010001));window.graph_selection()
    graphical=window.get_selection().resolve(window.current_run())
    typed=Selection(origin+1,origin+10001,(2,6)).resolve(window.current_run())
    assert graphical==typed
    window.close()


def test_portable_export_and_replay(tmp_path):
    make_demo(tmp_path/"source")
    with Project(tmp_path/"project") as p:
        p.scan([tmp_path/"source"]);run=p.runs()[0][0];roots=dict(p.roots)
    create_job(tmp_path/"job",run,roots,Selection(run.start_us,run.end_us,(2,6)),AnalysisRecipe())
    run_job(tmp_path/"job")
    export_bundle(tmp_path/"job",tmp_path/"bundle",settings={"log_frequency":True,"dpi":90})
    for name in ["figure.png","figure.pdf","figure.svg","job.json","result.h5","caption.txt","recipe.json","root_map_template.json"]:
        assert (tmp_path/"bundle"/name).stat().st_size>0
    numeric=read_json(tmp_path/"bundle/result_manifest.json")
    assert numeric["quality"]["verified_status_samples"]==7200
    # Replay the portable bundle after moving the source root, preserving source mtimes.
    import shutil
    shutil.copytree(tmp_path/"source",tmp_path/"moved")
    mapping={next(iter(roots)):str(tmp_path/"moved")}
    (tmp_path/"roots.json").write_text(json.dumps(mapping))
    subprocess.run([sys.executable,"-m","das_workbench","replay",str(tmp_path/"bundle"),"--output",str(tmp_path/"replay"),"--roots",str(tmp_path/"roots.json")],check=True,timeout=60)
    with h5py.File(tmp_path/"bundle/result.h5") as a,h5py.File(tmp_path/"replay/result.h5") as b:
        np.testing.assert_array_equal(a["welch/psd"][:],b["welch/psd"][:])
    assert not any(str(tmp_path) in (tmp_path/"bundle"/n).read_text() for n in ["job.json","result_manifest.json","recipe.json"])


def test_channel_expression_contract():
    assert parse_channels("1:21",30)==tuple(range(1,21))
    assert parse_channels("0,4,6",7)==(0,4,6)
