"""Source-Qt Stop responsiveness on the same public worker implementation."""
from pathlib import Path
import json,time,sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from das_workbench.public_ui import PublicWindow
from das_workbench.public_engine import dump
from das_workbench.native import sha


def main(config,output):
    c=json.loads(Path(config).read_text());case=next(x for x in c['cases'] if x['id']=='F02');out=Path(output);out.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]);w=PublicWindow();w.show();w.open_recording(case['path']);w.apply_recipe(json.loads(Path(case['recipe']).read_text()));app.processEvents()
    w.end.setValue(w.signals[0].n);w.start.setValue(0);w.bin.setValue(50)
    dest=out/'stopped_result';w.start_analysis(dest);deadline=time.monotonic()+30
    while 'statistics' not in w.status.text() and w.process and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
    assert w.process is not None and 'statistics' in w.status.text()
    before=w.status.text();heartbeat=[];timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:heartbeat.append(time.perf_counter()));timer.start()
    begin=time.perf_counter();w.stop_button.click();app.processEvents();ack=time.perf_counter()-begin;assert w.status.text().startswith('Stopping') and ack<1
    w.grab().save(str(out/'stop_acknowledged.png'))
    while w.process is not None and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
    end=time.perf_counter()-begin;timer.stop();assert w.process is None and not dest.exists() and not list(out.glob('.stopped_result.*'))
    result=dict(passed=True,frozen=bool(getattr(sys,'frozen',False)),environment='Qt/Cocoa public worker',
        acknowledgement_s=ack,completion_s=end,heartbeat_count=len(heartbeat),status_before=before,status_after=w.status.text(),
        no_completed_result=True,staging_removed=True,overview_resume_claim=False,limitation='Acknowledgement is separate from completion after bounded I/O; OS read instant not instrumented')
    dump(out/'measurement.json',result);w.close();app.processEvents();return 0


if __name__=='__main__':raise SystemExit(main(*sys.argv[1:]))
