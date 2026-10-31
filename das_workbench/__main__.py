from __future__ import annotations
import argparse
from pathlib import Path
import json
import multiprocessing
import tempfile
import sys
from .contracts import Selection,AnalysisRecipe,read_json,atomic_json,jsonable,utc_us,iso,run_from_dict,recipe_from_dict


def smoke(output, real_overview=None):
    """Exercise bundled dependencies and science without a developer environment."""
    import h5py
    import numpy as np
    import time
    from .fixtures import make_demo
    from .project import Project
    from .engine import create_job,run_job
    from .diagnostics import report
    from .exporter import export_bundle
    from PySide6.QtWidgets import QApplication
    from .ui import MainWindow
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    source=output/"synthetic"
    make_demo(source)
    with Project(output/"project") as p:
        imported=p.scan([source],deep=True)
        run=p.runs()[0][0]
        roots=dict(p.roots)
    s=Selection(run.start_us,run.end_us,(2,6))
    out=output/"analysis"
    if not (out/"job.json").exists():create_job(out,run,roots,s,AnalysisRecipe())
    completed=run_job(out)
    with h5py.File(out/"result.h5") as f:
        assert np.isfinite(f["welch/psd"][:]).all()
        assert f["stft/psd"].shape[0]==2
    if not all((output/"export"/name).exists() for name in ("figure.png","figure.pdf","figure.svg","bundle_checksums.json")):
        export_bundle(out,output/"export")
    app=QApplication.instance() or QApplication([])
    window=MainWindow(str(output/"project"));window.show();app.processEvents()
    window.channels.setText("2,6");window.analyse()
    deadline=time.monotonic()+90
    while (window.result is None or window.process is not None) and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    if window.result is None:
        raise RuntimeError("Packaged GUI/worker workflow failed: "+window.status.text())
    window.tabs.setCurrentWidget(window.spec);app.processEvents()
    window.grab().save(str(output/"packaged_window.png"))
    window.generate_overview(dict(trace_channel=6,channel_stride=2,time_bin_samples=10,title="Synthetic DAS | validation overview"))
    deadline=time.monotonic()+90
    while (window.overview_directory is None or window.process is not None) and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    if window.overview_directory is None:
        raise RuntimeError("Packaged overview workflow failed: "+window.status.text())
    from .overview import export_overview, make_overview_figure
    from .contracts import file_hash
    from .overview_evidence import evidence_status
    previous=Path(window.overview_directory);previous_sha=file_hash(previous/"overview.h5")
    full_extent=tuple(window.overview_canvas.figure.axes[0].get_xlim())
    window.overview_canvas.figure.axes[0].set_xlim(1,2)
    window.overview_canvas.figure.axes[1].set_xlim(3,4)
    window.start_worker(dict(task="overview_export",directory=str(previous),destination=str(output/"overview_export")))
    deadline=time.monotonic()+90
    while window.process is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    assert (output/"overview_export/overview.svg").exists()
    fresh=make_overview_figure(output/"overview_export")
    assert tuple(fresh.axes[0].get_xlim())==full_extent
    fresh.clear()
    window.generate_overview(dict(trace_channel=6,channel_stride=2,time_bin_samples=10))
    interrupted=Path(window.current_task['directory'])
    begin_stop=time.perf_counter();window.pause()
    acknowledgement=time.perf_counter()-begin_stop
    assert acknowledgement<1 and window.status.text().startswith('Stopping overview')
    assert window.pause_button.text()=='Stop overview'
    heartbeat=[]
    from PySide6.QtCore import QTimer
    heartbeat_timer=QTimer();heartbeat_timer.setInterval(10);heartbeat_timer.timeout.connect(lambda:heartbeat.append(time.perf_counter()));heartbeat_timer.start()
    deadline=time.monotonic()+90
    while window.process is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    heartbeat_timer.stop()
    assert window.process is None and not interrupted.exists() and heartbeat
    assert file_hash(previous/'overview.h5')==previous_sha
    stop_completion=time.perf_counter()-begin_stop
    window.close();app.processEvents()
    window=MainWindow(str(output/'project'));window.show();app.processEvents()
    assert any('Interrupted — regenerate' in window.jobs.item(i).text() for i in range(window.jobs.count()))
    window.show_fibre_overview(previous)
    real_status=None
    if real_overview:
        window.show_fibre_overview(real_overview);app.processEvents()
        real_status=evidence_status(real_overview)
        assert real_status['status']=='passed'
        assert 'validation: passed' in window.fibre_note.text()
        window.grab().save(str(output/'packaged_real_overview.png'))
    window.grab().save(str(output/"packaged_overview.png"))
    window.close();app.processEvents()
    diagnostic=report(output/"diagnostics.json")
    from .science import periodograms
    from .fixtures import signal_fixture
    sx=signal_fixture(200,10,4)[:,None,:]
    _,_,gpu_report=periodograms(sx,200,800,backend="mlx")
    atomic_json(output/"smoke.json",dict(passed=True,imported=imported,job_state=completed["state"],worker_workflow=True,overview_worker_and_export=True,metal_report=gpu_report,
                                         frozen=bool(getattr(sys,"frozen",False)),diagnostics="diagnostics.json",app_version=__import__('das_workbench').__version__,
                                         stop_acknowledgement_seconds=acknowledgement,stop_completion_seconds=stop_completion,gui_heartbeat_count=len(heartbeat),
                                         interrupted_reopens_as_regenerate=True,previous_result_unchanged=True,full_extent_after_zoom_export=True,
                                         independently_validated_real_overview=real_status))
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description="DAS Workbench: GUI and shared-engine replay")
    sub=parser.add_subparsers(dest="command")
    gui=sub.add_parser("gui");gui.add_argument("--project")
    index=sub.add_parser("index");index.add_argument("--project",required=True);index.add_argument("--deep",action="store_true");index.add_argument("roots",nargs="*")
    ana=sub.add_parser("analyse");ana.add_argument("--project",required=True);ana.add_argument("--run",type=int,default=0);ana.add_argument("--start");ana.add_argument("--end");ana.add_argument("--channels",default="0");ana.add_argument("--recipe");ana.add_argument("--output",required=True)
    resume=sub.add_parser("resume");resume.add_argument("directory");resume.add_argument("--roots")
    replay=sub.add_parser("replay");replay.add_argument("directory");replay.add_argument("--output",required=True);replay.add_argument("--roots")
    export=sub.add_parser("export");export.add_argument("directory");export.add_argument("--output",required=True);export.add_argument("--channel-index",type=int,default=0);export.add_argument("--settings")
    demo=sub.add_parser("demo");demo.add_argument("directory")
    doctor=sub.add_parser("doctor");doctor.add_argument("--output")
    sm=sub.add_parser("smoke");sm.add_argument("--output",required=True);sm.add_argument("--real-overview",help="Open an independently validated saved overview in the packaged GUI")
    ov=sub.add_parser("overview");ov.add_argument("--project",required=True);ov.add_argument("--run",type=int,default=0)
    ov.add_argument("--start");ov.add_argument("--end");ov.add_argument("--settings");ov.add_argument("--output",required=True);ov.add_argument("--plan",action="store_true",help="Print metadata-only dimensions and limits without reading waveforms");ov.add_argument("--accept-suggested-bin",action="store_true",help="Explicitly accept planner suggestion; preserves native trace and RMS")
    oe=sub.add_parser("overview-export");oe.add_argument("directory");oe.add_argument("--output",required=True);oe.add_argument("--settings")
    ore=sub.add_parser("overview-replay");ore.add_argument("directory");ore.add_argument("--output",required=True);ore.add_argument("--roots")
    pub=sub.add_parser("public-gui");pub.add_argument("--recording");pub.add_argument("--recipe")
    pw=sub.add_parser("public-workflow");pw.add_argument("--config",required=True);pw.add_argument("--output",required=True)
    ps=sub.add_parser('public-stop-check');ps.add_argument('--config',required=True);ps.add_argument('--output',required=True)
    mc=sub.add_parser('maintenance-check',help='Focused maintenance checks using synthetic fixtures only');mc.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    if args.command=='maintenance-check':
        from validation.limited_corrections import execute
        return execute(args.output)
    if args.command=='public-stop-check':
        from validation.public_stop_check import main as stop_main
        return stop_main(args.config,args.output)
    if args.command=="public-workflow":
        from .public_workflow import execute
        return execute(args.config,args.output)
    if args.command=="public-gui":
        from PySide6.QtWidgets import QApplication
        from .public_ui import PublicWindow
        app=QApplication.instance() or QApplication([]);window=PublicWindow();window.show()
        if args.recording:window.open_recording(args.recording)
        if args.recipe:window.apply_recipe(read_json(args.recipe))
        return app.exec()
    if args.command in (None,"gui"):
        from .ui import launch
        return launch(getattr(args,"project",None))
    if args.command=="smoke":return smoke(args.output,args.real_overview)
    if args.command=="doctor":
        from .diagnostics import report
        print(json.dumps(report(args.output),indent=2));return 0
    if args.command=="overview":
        from .project import Project
        from .overview import plan_overview
        from .overview_jobs import run_overview_job
        with Project(args.project) as p:
            run=p.runs()[0][args.run];roots=dict(p.roots);doi=p.document.get("source_doi")
        start=utc_us(args.start) if args.start else run.start_us
        end=utc_us(args.end) if args.end else min(run.end_us,start+60_000_000)
        settings,selection,plan=plan_overview(run,start,end,read_json(args.settings) if args.settings else None)
        decision=dict(action="requested settings accepted unchanged",requested=dict(settings),accepted=dict(settings))
        if args.accept_suggested_bin and plan['suggested_bin_samples'] is not None:
            settings['time_bin_samples']=plan['suggested_bin_samples']
            decision.update(action="explicit CLI acceptance of suggested display bin",accepted=dict(settings))
            settings,selection,plan=plan_overview(run,start,end,settings)
        if args.plan:
            print(json.dumps(dict(selection=selection,plan=plan,display_planning=decision),indent=2));return 0 if plan['allowed'] else 2
        if not plan['allowed']:
            print(" ".join(plan['blocking_reasons']),file=sys.stderr);return 2
        print(run_overview_job(args.output,run,roots,start,end,settings,doi,planning_decision=decision));return 0
    if args.command=="overview-export":
        from .overview import export_overview
        print(export_overview(args.directory,args.output,read_json(args.settings) if args.settings else None));return 0
    if args.command=="overview-replay":
        from .overview_jobs import run_overview_job
        directory=Path(args.directory);identity=read_json(directory/"overview_recipe.json")
        roots=read_json(args.roots) if args.roots else read_json(directory/"local_roots.json")
        print(run_overview_job(args.output,run_from_dict(identity["run"]),roots,identity["start_us"],identity["end_us"],identity["settings"],identity.get("source_doi")));return 0
    if args.command=="demo":
        from .fixtures import make_demo
        print("\n".join(str(p) for p in make_demo(args.directory)));return 0
    if args.command=="index":
        from .project import Project
        with Project(args.project) as p:
            report=p.scan(args.roots,deep=args.deep)
            report["stream_summaries"]=[dict(index=i,id=r.id,files=len(r.files),rate=r.first.rate,
              channels=r.first.channels,start=iso(r.start_us),end=iso(r.end_us),acquisition_id=r.first.acquisition_id) for i,r in enumerate(p.runs()[0])]
        print(json.dumps(report,indent=2));return int(bool(report["errors"]))
    if args.command=="analyse":
        from .project import Project
        from .engine import create_job,run_job
        # CLI channel parsing deliberately does not import Qt.
        from .selection import parse_channels
        with Project(args.project) as p:
            run=p.runs()[0][args.run];roots=dict(p.roots);doi=p.document.get("source_doi")
        recipe=recipe_from_dict(read_json(args.recipe)) if args.recipe else AnalysisRecipe()
        selection=Selection(utc_us(args.start) if args.start else run.start_us,
                            utc_us(args.end) if args.end else min(run.end_us,run.start_us+60_000_000),
                            parse_channels(args.channels,run.first.channels))
        create_job(args.output,run,roots,selection,recipe,source_doi=doi)
        job=run_job(args.output);print(json.dumps({k:job[k] for k in ["id","state","completed_units"]}));return 0
    if args.command in ("replay","resume"):
        from .engine import create_job,run_job
        old=Path(args.directory)
        roots=read_json(args.roots) if args.roots else read_json(old/"local_roots.json")
        target=old
        if args.command=="replay":
            identity=read_json(old/"job.json")["identity"]
            s=identity["selection"];selection=Selection(s["start_us"],s["end_us"],tuple(s["channels"]))
            create_job(args.output,run_from_dict(identity["run"]),roots,selection,recipe_from_dict(identity["recipe"]),identity.get("source_doi"))
            target=args.output
        job=run_job(target,roots);print(job["state"]);return 0
    if args.command=="export":
        from .exporter import export_bundle
        print(export_bundle(args.directory,args.output,args.channel_index,read_json(args.settings) if args.settings else None));return 0
    return 0


if __name__=="__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
