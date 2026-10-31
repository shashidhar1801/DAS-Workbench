"""Focused v0.3.4 maintenance checks: tiny fixtures, actual Qt/worker paths.
No original recordings, accepted results or publication assets are written.
"""
from pathlib import Path
from datetime import datetime,timedelta
from decimal import Decimal
import hashlib,json,sys,time,traceback
import h5py,numpy as np
from das_workbench import __version__
from das_workbench.native import open_recording
from das_workbench.public_selection import time_snapshot,absolute_interval

def dump(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def timezone_cases(output,check_labels=True):
    out=Path(output);out.mkdir(parents=True,exist_ok=True);records=[]
    for case,zone in [('A1','Z'),('A2','+00:00'),('A3','+08:00'),('A4','-05:00'),('A5','')]:
        origin='2022-01-01T12:34:56.125'+zone
        end=(datetime.fromisoformat(origin)+timedelta(seconds=2.048)).isoformat(timespec='microseconds')
        if zone=='Z':end=end.replace('+00:00','Z')
        path=out/(case+'.h5');raw=np.arange(4096,dtype=np.float32).reshape(2048,2)
        with h5py.File(path,'w') as h:
            d=h.create_dataset('Recorder-C',data=raw)
            for k,v in {'Sample Rate (Hz)':1000.,'Start Time':origin,'End Time':end,'Signal':'Sound Pressure','Unit':'Pa'}.items():d.attrs[k]=v
        signal=open_recording(path)[0];assert signal.origin==origin
        expected='Explicit timezone-aware timestamp attributes' if zone else 'Recording clock; timezone and external synchronization not established'
        if check_labels:assert signal.clock==expected,(case,signal.clock)
        intervals=[]
        for a,b in [(1,101),(1000,1100),(1948,2048)]:
            snap=time_snapshot(signal,a,b)
            for _ in range(10):
                bounds=absolute_interval(signal,snap['timestamp'],snap['duration_display'],snap)
                assert bounds==(a,b);snap=time_snapshot(signal,*bounds)
            edited=time_snapshot(signal,Decimal(a)+Decimal('0.25'),Decimal(b)+Decimal('0.25'))
            if b<signal.n:
                assert absolute_interval(signal,edited['timestamp'],snap['duration_display'],snap)==(a+1,b+1)
            intervals.append(dict(bounds=list(bounds),snapshot=snap,unchanged_reapplications=10))
        decoded=signal.read(0,signal.n,[0,1]);np.testing.assert_array_equal(decoded,raw)
        records.append(dict(case=case,input_origin=origin,observed_origin=signal.origin,clock=signal.clock,expected_clock=expected,origin_preserved=True,intervals=intervals,quantity=signal.quantity,unit=signal.unit,calibration=signal.calibration,pressure_eligible=signal.pressure,decoded_dtype=str(decoded.dtype),decoded_sha256=hashlib.sha256(decoded.tobytes()).hexdigest()))
    result=dict(version=__version__,frozen=bool(getattr(sys,'frozen',False)),cases=records,checked_new_labels=check_labels,total_unchanged_reapplications=150)
    dump(out/'timezone_checks.json',result);return result


def phase_error_sequence(output):
    from PySide6 import QtCore,QtWidgets as Q
    from das_workbench.ui import MainWindow
    from das_workbench.fixtures import make_demo
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    app=Q.QApplication.instance() or Q.QApplication([])
    w=MainWindow(str(out/'project'));w.resize(1480,920);w.show()
    captures=[]
    def capture(widget,name):
        app.processEvents();path=out/name;assert widget.grab().save(str(path))
        captures.append(dict(file=name,sha256=digest(path),method='Actual QWidget.grab; no overlays, crop replacement or bitmap upsampling',version=__version__,frozen=bool(getattr(sys,'frozen',False)),qt_platform=app.platformName(),logical_size=[widget.width(),widget.height()],device_pixel_ratio=widget.devicePixelRatioF()))
    def wait_until(predicate,seconds=30):
        deadline=time.monotonic()+seconds
        while not predicate() and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
        assert predicate(),'Timed out waiting for actual Qt/worker state'
    refreshes=[];original_refresh=w.refresh_jobs
    def refresh():refreshes.append(True);return original_refresh()
    w.refresh_jobs=refresh
    scheduled=[];original_next=w.next_job
    def next_job():scheduled.append(True);return original_next()
    w.next_job=next_job
    # The real production worker fails on a deliberately nonexistent input path.
    # Both path styles are fixture text, not evidence of a Windows execution.
    fake_path=r'/Users/maintenance-fixture/missing/C:\Users\maintenance-fixture\recording'
    sentinel='Scientific metadata remains available after a failed task.'
    w.details.setPlainText(sentinel);w.tabs.setCurrentWidget(w.psd);tab_before=w.tabs.currentIndex()
    terminals=[]
    class ObserveQueue:
        def __init__(self,q):self.q=q
        def get_nowait(self):
            item=self.q.get_nowait()
            if item.get('type')=='error':terminals.append(item)
            return item
    assert not w.error_details_action.isEnabled()
    w.start_worker(dict(task='result',directory=fake_path));worker=w.process;w.messages=ObserveQueue(w.messages)
    wait_until(lambda:w.process is None)
    assert len(terminals)==1;terminal=terminals[0]
    assert '/Users/maintenance-fixture/' in terminal['message'] and 'C:' in terminal['message'] and 'Traceback' in terminal['traceback']
    original=terminal['message']+'\n\n'+terminal['traceback']
    assert w.last_error==original
    default_status=w.status.text()
    assert default_status=='The operation could not be completed. Open Error details for diagnostic information.'
    for token in ['/Users/','C:\\Users','Traceback','FileNotFoundError']:assert token not in default_status
    assert w.details.toPlainText()==sentinel and w.tabs.currentIndex()==tab_before
    assert app.activeModalWidget() is None
    assert w.current_task is None and w.preview_button.isEnabled() and not worker.is_alive()
    assert w.pause_button.text()=='Pause' and w.cancel_button.isVisible()
    assert refreshes and w.error_details_action.isEnabled()
    capture(w,'01_default_worker_error.png')
    dialog_check={};dialog_errors=[];saved=out/'saved_worker_diagnostic.txt'
    original_save=Q.QFileDialog.getSaveFileName
    Q.QFileDialog.getSaveFileName=lambda *a,**k:(str(saved),'Text (*.txt)')
    def inspect_dialog():
        dialog=app.activeModalWidget()
        try:
            assert isinstance(dialog,Q.QDialog) and dialog.windowTitle()=='Last error details'
            view=dialog.findChild(Q.QPlainTextEdit,'error_diagnostic')
            assert view.isReadOnly() and view.toPlainText()==original
            capture(dialog,'02_explicit_error_details.png')
            save=next(b for b in dialog.findChildren(Q.QPushButton) if b.text()=='Save diagnostic report…');save.click()
            assert saved.read_bytes()==original.encode('utf8')
            dialog_check.update(read_only_plain_text=True,original_message_retained=True,complete_traceback_retained=True,saved_diagnostic_exact_bytes=True,saved_sha256=digest(saved),action='Explicit Error details -> Save diagnostic report; file destination supplied by automation')
        except BaseException as exc:dialog_errors.append(repr(exc))
        finally:
            if dialog:dialog.accept()
    QtCore.QTimer.singleShot(150,inspect_dialog)
    w.error_details_action.trigger();Q.QFileDialog.getSaveFileName=original_save
    assert not dialog_errors,dialog_errors
    assert w.details.toPlainText()==sentinel and w.tabs.currentIndex()==tab_before
    wait_until(lambda:bool(scheduled))
    # Recover through the ordinary scanner/worker, not a fake success callback.
    source=out/'synthetic_sources';make_demo(source)
    w.scan([str(source)],deep=True)
    assert 'last error' in w.error_details_action.text().lower() and w.last_error==original
    wait_until(lambda:w.process is None)
    assert len(w.runs)==1 and len(w.runs[0].files)==3 and w.preview_button.isEnabled()
    success=w.status.text();assert '3 recordings' in success and '0 import errors' in success
    assert w.last_error==original and 'last error' in w.error_details_action.text().lower()
    assert app.activeModalWidget() is None
    wait_until(lambda:len(scheduled)>=2)
    capture(w,'03_success_after_error.png')
    def warning_action(action):
        observed=[];errors=[]
        def close_warning():
            box=app.activeModalWidget()
            try:
                assert isinstance(box,Q.QMessageBox);observed.append(box.text())
            except BaseException as exc:errors.append(repr(exc))
            finally:
                if box:box.accept()
        QtCore.QTimer.singleShot(100,close_warning);action()
        assert not errors and len(observed)==1,(errors,observed)
        return observed[0]
    try:raise RuntimeError(r'opaque exception /srv/private/input and D:\private\input <diagnostic text>')
    except RuntimeError as exc:
        raw_trace=''.join(traceback.format_exception(exc))
        direct_text=warning_action(lambda:w.error(exc))
    assert direct_text==default_status and w.status.text()==default_status and w.last_error==raw_trace
    # An existing application-authored validation path remains actionable.
    validation_text=warning_action(w.export)
    assert validation_text=='Calculate or open a saved result before exporting.'
    assert w.last_error==raw_trace
    assert any(a.text()=='Diagnostics' for a in w.findChildren(Q.QToolBar)[0].actions())
    result=dict(passed=True,version=__version__,frozen=bool(getattr(sys,'frozen',False)),qt_platform=app.platformName(),qt_style=app.style().objectName(),executable=str(Path(sys.executable).resolve()),executable_sha256=digest(sys.executable),worker_error=dict(actual_worker='result reader on nonexistent fixture path',actual_terminal_observed=True,actual_MainWindow_poll=True,default_status=default_status,no_raw_paths_or_exception_text=True,no_automatic_modal_dialog=True,metadata_content_unchanged=True,scientific_tab_not_forced=True,complete_diagnostic_sha256=hashlib.sha256(original.encode()).hexdigest(),process_joined=True,current_task_cleared=True,controls_reenabled=True,pause_cancel_controls_restored=True,job_refresh_called=True,next_job_scheduled=True),explicit_diagnostics=dialog_check,recovery=dict(task='ordinary deep catalogue scan of three synthetic HDF5 files',status=success,completed=True,last_error_identified_as_historical=True),direct_handler=dict(raw_exception_summary=direct_text,raw_exception_and_traceback_preserved=True,trusted_validation=validation_text,environment_diagnostics_action_retained=True),captures=captures,limitations=['Author automation with supplied diagnostic-save filename; not a human usability observation','POSIX and Windows paths are synthetic strings; actual platform is recorded above','No original recording analysis or scientific benchmark executed'])
    dump(out/'phase_error_checks.json',result)
    w.close();app.processEvents();return result


def execute(output):
    out=Path(output)
    if out.exists():raise FileExistsError('Use an unused maintenance-check output directory')
    out.mkdir(parents=True)
    result=dict(timezone=timezone_cases(out/'timezone'),phase_error=phase_error_sequence(out/'phase_error'))
    dump(out/'Focused_Runtime_Checks.json',result)
    print('Focused maintenance checks passed; version',__version__,'packaged',bool(getattr(sys,'frozen',False)),flush=True)
    return 0

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--timezone-only',action='store_true');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.timezone_only:timezone_cases(args.output,check_labels=False)
    else:execute(args.output)
