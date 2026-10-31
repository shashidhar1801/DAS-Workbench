"""Spawn-compatible worker entry; descriptors in, bounded messages out."""
from pathlib import Path
import traceback
import queue
from .contracts import run_from_dict,Selection,recipe_from_dict,jsonable,digest


def worker_main(request, messages, cancel, pause):
    def progress(done,total,label):
        try: messages.put_nowait(dict(type="progress",done=done,total=total,label=label))
        except queue.Full: pass
    def control():
        return "cancel" if cancel.is_set() else ("pause" if pause.is_set() else None)
    try:
        task = request["task"]
        if task == "scan":
            from .project import Project
            with Project(request["project"]) as p:
                result = p.scan(request.get("paths",()),deep=request.get("deep",False),progress=progress,cancelled=cancel.is_set)
                runs,conflicts = p.runs()
                result.update(runs_data=jsonable(runs),roots=p.roots,conflicts=conflicts)
        elif task == "analyse":
            from .engine import run_job
            result = run_job(request["directory"],roots=request.get("roots"),progress=progress,control=control)
        elif task == "preview":
            from .engine import waveform_preview
            from .adapter import assert_signature
            from .cache import DisplayCache
            s = request["selection"]
            run = run_from_dict(request["run"])
            for source in run.files:
                if source.end_us>s["start_us"] and source.first_us<s["end_us"]:assert_signature(source,request["roots"])
            cache = DisplayCache(request["cache"]) if request.get("cache") else None
            key = digest(dict(algorithm="display-minmax-1",run=request["run"],selection=s,quantity=request["quantity"]))
            result = cache.get(key) if cache else None
            if result is None:
                result = waveform_preview(run,request["roots"],
                             Selection(s["start_us"],s["end_us"],tuple(s["channels"])),request["quantity"],control=control)
                result["display_cache_hit"] = False
                if cache:cache.put(key,result)
        elif task == "export":
            from .exporter import export_bundle
            result = export_bundle(request["directory"],request["destination"],request.get("channel_index",0),request.get("settings"))
        elif task == "overview":
            from .overview_jobs import run_overview_job
            result = run_overview_job(request["directory"],run_from_dict(request["run"]),request["roots"],
                request["start_us"],request["end_us"],request.get("settings"),request.get("source_doi"),progress,control,planning_decision=request.get("planning_decision"))
        elif task == "overview_export":
            from .overview import export_overview
            result = export_overview(request["directory"],request["destination"],request.get("settings"))
        elif task == "result":
            from .exporter import result_view
            result = result_view(request["directory"],request.get("channel_index",0))
        elif task == "diagnostics":
            from .diagnostics import report
            result = report(request.get("destination"))
        else:
            raise ValueError(f"Unknown worker task: {task}")
        messages.put(dict(type="result",task=task,result=result),timeout=10)
    except InterruptedError as exc:
        messages.put(dict(type="stopped" if request.get("task")=="overview" else "error",message=str(exc),traceback=traceback.format_exc()),timeout=10)
    except BaseException as exc:
        messages.put(dict(type="error",task=request.get("task"),message=f"{type(exc).__name__}: {exc}",traceback=traceback.format_exc()),timeout=10)
