"""Measure Qt Stop responsiveness after actual real-data overview progress."""
from pathlib import Path
import time
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from das_workbench.project import Project
from das_workbench.ui import MainWindow
from das_workbench.contracts import atomic_json,read_json
from das_workbench.overview_jobs import record_path


def main():
    output=Path('reports/v0.2.1/stop_real_io');output.mkdir(parents=True,exist_ok=True)
    with Project('examples/OOI_project') as p:
        run=next(r for r in p.runs()[0] if float(r.first.rate)==1000)
        source=Path(p.roots[run.first.root_id])/run.first.relative_path
    with Project(output/'project') as p:p.scan([str(source)],deep=True)
    app=QApplication.instance() or QApplication([]);w=MainWindow(str(output/'project'));w.show();app.processEvents()
    w.generate_overview(dict(trace_channel=14692,channel_stride=16,time_bin_samples=10))
    dest=Path(w.current_task['directory']);deadline=time.monotonic()+60
    while w.process is not None and not (w.progress.maximum()>1 and w.progress.value()>0):
        app.processEvents();time.sleep(.001)
        if time.monotonic()>deadline:raise TimeoutError('No real read progress received')
    assert w.process is not None and w.process.is_alive()
    last_progress=w.progress.value();total=w.progress.maximum();assert 0<last_progress<total
    heartbeat=[];timer=QTimer();timer.setInterval(0);timer.timeout.connect(lambda:heartbeat.append(time.perf_counter()));timer.start()
    start=time.perf_counter();w.pause();app.processEvents();ack=time.perf_counter()-start
    assert w.status.text().startswith('Stopping overview') and ack<1
    w.grab().save(str(output/'stopping_real_io.png'))
    while w.process is not None:
        app.processEvents();time.sleep(.001)
        if time.monotonic()>deadline:raise TimeoutError('Stop completion deadline')
    timer.stop();completion=time.perf_counter()-start
    assert not dest.exists() and read_json(record_path(dest))['state']=='interrupted'
    assert heartbeat
    w.close();app.processEvents()
    atomic_json(output/'measurement.json',dict(status='passed',app_version='0.2.1',environment='Source Qt Cocoa UI on same Mac as packaged smoke; spawned scientific worker; first real 1000-Hz file',source_relative_path=source.name,
        native_samples_requested=30000,channels=32600,progress_when_stop_requested=[last_progress,total],acknowledgement_including_paint_and_event_service_seconds=ack,
        stop_completion_seconds=completion,heartbeat_count_after_request=len(heartbeat),heartbeat_timer_interval_ms=0,new_result_published=False,persisted_state='interrupted',
        interpretation='Single uncontrolled actual source-read/compute observation. A bounded operation was in flight after real progress; exact OS blocking instant not instrumented. Not a worst-case disk-latency guarantee.'))

if __name__=='__main__':main()
