"""Automated execution of the same visible Qt actions used by researchers.

Dialog choices are supplied by the harness. Analysis, validation and export
always use the production UI's spawned workers; no harness numerical plots.
"""
from pathlib import Path
import json,time,sys,os,threading,gc
import psutil
from PySide6.QtWidgets import QApplication,QPushButton,QFileDialog
from PySide6.QtCore import QTimer,QPoint
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QWidget
from .public_ui import PublicWindow
from .public_engine import dump,evidence
from .native import sha


class Observe:
    def __init__(self):self.samples=[];self.alive=True;self.phase='idle';self.parent=psutil.Process();self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
    def loop(self):
        while self.alive:
            try:
                child=sum(p.memory_info().rss for p in self.parent.children(recursive=True) if p.is_running())
                self.samples.append((self.phase,time.perf_counter(),self.parent.memory_info().rss,child))
            except psutil.Error:pass
            time.sleep(.02)
    def finish(self):
        self.alive=False;self.thread.join();out={}
        for phase in {s[0] for s in self.samples}:
            vals=[s for s in self.samples if s[0]==phase];out[phase]=dict(samples=len(vals),gui_peak_rss=max(s[2] for s in vals),workers_peak_rss=max(s[3] for s in vals),contemporaneous_tree_peak_rss=max(s[2]+s[3] for s in vals),raw_samples=[dict(t=s[1],parent_rss=s[2],children_rss=s[3],tree_rss=s[2]+s[3]) for s in vals])
        return out


def capture(w,path,app):
    app.processEvents();w.grab().save(str(path))
    if not path.stem.startswith(('06_','07_')):return
    ratio=4;image=QImage(w.width()*ratio,w.height()*ratio,QImage.Format_ARGB32);image.setDevicePixelRatio(ratio);image.fill(0xffffffff)
    painter=QPainter(image);w.render(painter,QPoint(0,0));painter.end();image.save(str(path.with_name(path.stem+'_qt4x.png')))
    dump(path.with_suffix('.capture.json'),dict(method='Actual QWidget.render into a device-pixel-ratio 4 image; widgets repainted, no bitmap resampling or label replacement',logical_size=[w.width(),w.height()],native_grab_dpr=w.devicePixelRatioF(),render_dpr=ratio,pixels=[image.width(),image.height()],version='0.3.4'))


def execute(config,output):
    config=json.loads(Path(config).read_text());out=Path(output);out.mkdir(parents=True,exist_ok=True);app=QApplication.instance() or QApplication([]);records=[]
    def wait(w):
        deadline=time.monotonic()+1200
        while w.process is not None and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        app.processEvents()
        if w.process is not None:raise RuntimeError('GUI worker timeout')
        if w.last_error:raise RuntimeError(w.last_error)
    for case in config['cases']:
        for run in range(config.get('repetitions',1)):
            name=case['id'];target=out/name/f'run{run}';target.mkdir(parents=True,exist_ok=False);w=PublicWindow();w.show();app.processEvents();obs=Observe();heartbeat=[]
            timer=QTimer();timer.setInterval(50);timer.timeout.connect(lambda:heartbeat.append(time.perf_counter()));timer.start()
            try:
                obs.phase='metadata';w.open_recording(case['path']);app.processEvents();capture(w,target/'01_metadata.png',app)
                if config.get('timestamp_checks'):
                    from validation.public_timestamp import exercise
                    exercise(w,app,target)
                if config.get('normal_controls'):
                    # No recipe loaded. Bounded native selection through the basic controls.
                    s=w.signals[0];assert w.fmax.value()<=s.fs/2
                    w.start.setValue(5);w.end.setValue(5+int(2/s.dt));w.sync_time_fields()
                    next(b for b in w.findChildren(QPushButton) if b.text()=='Apply timestamp').click()
                    coord=s.coordinates[w.coordinate.currentText()];w.coord_low.setValue(float(min(coord[:16]))-1e-5);w.coord_high.setValue(float(max(coord[:16]))+1e-5);w.coord_trace.setValue(float(coord[8]));w.apply_coordinates()
                    assert w.indices()==list(range(16));w.bin.setValue(10);w.stride.setValue(1)
                    w.history.append('basic timestamp/duration/coordinate controls; no preloaded recipe')
                else:
                    recipe=json.loads(Path(case['recipe']).read_text());w.apply_recipe(recipe)
                if not config.get('normal_controls'):
                    expected=(w.start.value(),w.end.value());button=next(b for b in w.findChildren(QPushButton) if b.text()=='Apply timestamp')
                    for _ in range(10):button.click();app.processEvents();assert (w.start.value(),w.end.value())==expected
                app.processEvents();obs.phase='preview';w.preview();app.processEvents();capture(w,target/'02_selection_preview.png',app)
                p=w.show_plan()
                if not p or not p['allowed']:raise RuntimeError('Plan blocked: '+str(p))
                dump(target/'accepted_recipe.json',w.recipe());obs.phase='analyse';w.start_analysis(target/'result');wait(w);app.processEvents();w.canvas.figure.savefig(target/'review.png',dpi=130);capture(w,target/'03_result.png',app)
                if config.get('validate',True):
                    obs.phase='validate';w.validate_button.click();wait(w)
                    if evidence(w.result_directory)['status']!='passed':raise RuntimeError('Independent gate did not pass: '+json.dumps(evidence(w.result_directory)))
                    w.tabs.setCurrentIndex(2);app.processEvents();capture(w,target/'04_validation.png',app)
                if config.get('export',True):
                    # Preview zoom is intentionally irrelevant to export.
                    w.canvas.figure.axes[0].set_xlim(.5,1);obs.phase='export';w.presentation.setCurrentIndex(w.presentation.findData(case.get('presentation','archive')));w.start_export(target/'bundle');wait(w)
                    obs.phase='reopen';w.open_result(target/'bundle');app.processEvents();capture(w,target/'05_reopened.png',app)
                    if name=='F02' and not config.get('normal_controls'):
                        w.resize(1020,1100);w.findChild(__import__('PySide6.QtWidgets',fromlist=['QSplitter']).QSplitter).setSizes([500,500]);app.processEvents()
                        capture(w,target/'06_workflow_selection.png',app)
                        w.tabs.setCurrentIndex(2);app.processEvents();capture(w,target/'07_workflow_verification.png',app)
                    assert sha(target/'bundle/numeric.h5')==sha(target/'result/numeric.h5')
                    for f,digest in json.loads((target/'bundle/bundle_checksums.json').read_text()).items():assert sha(target/'bundle'/f)==digest
                if not config.get('normal_controls') and name!='S01':
                    w.presentation.setCurrentIndex(w.presentation.findData('archive'));obs.phase='overview_export';w.start_export(target/'overview_bundle');wait(w)
                    w.open_result(target/'overview_bundle');app.processEvents();capture(w,target/'08_overview_reopened.png',app)
                    assert sha(target/'overview_bundle/numeric.h5')==sha(target/'result/numeric.h5')
                    w.open_result(target/'bundle');app.processEvents()
                record=dict(case=name,run=run,frozen=bool(getattr(sys,'frozen',False)),qt_platform=app.platformName(),version='0.3.4',
                    workflow=w.history,timings=w.timings,resources=obs.finish(),rss_method='psutil parent + recursive child RSS sampled every20ms; aggregate child peak, not unique physical memory',
                    cache_conditions='OS cache uncontrolled; no eviction; initial run not labelled cold',heartbeat_count=len(heartbeat),
                    max_event_loop_gap_s=max([b-a for a,b in zip(heartbeat,heartbeat[1:])] or [0]),
                    environment=dict(pythonpath_present='PYTHONPATH' in os.environ,pythonhome_present='PYTHONHOME' in os.environ,virtualenv_present='VIRTUAL_ENV' in os.environ),
                    result_numeric_sha256=sha(target/'result/numeric.h5'),validation=evidence(target/'result')['status'])
                dump(target/'workflow.json',record);records.append(record);dump(out/'workflow_summary.json',records)
            except Exception as e:
                obs.finish();dump(target/'failure.json',dict(error=str(e),history=w.history,status=w.status.text()));raise
            finally:timer.stop();w.close();app.processEvents();del w;gc.collect()
    return 0
