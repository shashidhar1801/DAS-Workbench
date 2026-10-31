"""Native public-recording workspace inside DAS Workbench's Qt desktop app."""
from pathlib import Path
import json, multiprocessing as mp, queue, time, traceback
import numpy as np
from PySide6 import QtCore, QtWidgets as Q
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg,NavigationToolbar2QT
from matplotlib.figure import Figure
from .native import open_recording,plain
from .public_selection import interval_indices,coordinate_indices,qualify_review,time_snapshot,absolute_interval
from .public_engine import plan,analyse,load_result,evidence,attach,dump,accepted_samples
from .public_figures import figure,export
from .presentation import FORMAT_NAMES,plan_text,verification_html,status_name,concise_error,about_text


def public_worker(request,q,stop):
    def control():
        if stop.is_set():raise InterruptedError('Stopped; regenerate')
    def progress(x):q.put(('progress',x))
    try:
        kind=request['kind'];start=time.perf_counter()
        if kind=='analyse': result=analyse(request['path'],request['recipe'],request['output'],control,progress)
        elif kind=='export':result=export(request['directory'],request['output'],progress,control,request.get('presentation'))
        elif kind=='validate':
            from validation.public_reference import validate
            result=validate(request['path'],request['directory'],control,progress);control();attach(request['directory'],result)
        else:raise ValueError(kind)
        q.put(('done',dict(kind=kind,output=request.get('output'),elapsed=time.perf_counter()-start,result=result)))
    except InterruptedError:q.put(('stopped','Interrupted — regenerate; no new completed result'))
    except Exception:q.put(('error',traceback.format_exc()))


class PublicWindow(Q.QMainWindow):
    def __init__(self,parent=None):
        super().__init__(parent);Q.QApplication.instance().setStyle('Fusion');self.setWindowTitle('DAS Workbench — Recording analysis');self.resize(1350,930)
        self.setStyleSheet('QWidget { font-size: 17px; } QPushButton { padding: 4px; }');self.path=None;self.signals=[];self.result_directory=None;self.recipe_base={};self.process=None;self.last_error=None;self.history=[];self.timings={}
        root=Q.QWidget();self.setCentralWidget(root);v=Q.QVBoxLayout(root);bar=Q.QGridLayout();v.addLayout(bar)
        for i,(label,slot) in enumerate([('Open recording…',self.choose_recording),('Open folder…',self.choose_folder),('Load settings…',self.choose_recipe),('Save settings…',self.save_recipe),('Open saved result…',self.choose_result),('About',lambda:Q.QMessageBox.information(self,'About DAS Workbench',about_text()))]):
            b=Q.QPushButton(label);b.clicked.connect(slot);bar.addWidget(b,i//3,i%3)
        splitter=Q.QSplitter();v.addWidget(splitter,1);left=Q.QWidget();lv=Q.QVBoxLayout(left);scroll=Q.QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(left);scroll.setMinimumWidth(450);shell=Q.QWidget();outer=Q.QVBoxLayout(shell);outer.setContentsMargins(0,0,0,0);outer.addWidget(scroll,1);splitter.addWidget(shell)
        self.name=Q.QLabel('Open a supported recording to begin');self.name.setWordWrap(True);self.name.setMaximumWidth(430);self.name.setSizePolicy(Q.QSizePolicy.Ignored,Q.QSizePolicy.Preferred);lv.addWidget(self.name)
        lv.addWidget(Q.QLabel('<b>Select time and cable section</b>'));self.basic=Q.QFormLayout();self.basic.setFieldGrowthPolicy(Q.QFormLayout.AllNonFixedFieldsGrow);self.basic.setRowWrapPolicy(Q.QFormLayout.WrapLongRows);lv.addLayout(self.basic)
        self.seconds=Q.QDoubleSpinBox();self.duration=Q.QDoubleSpinBox()
        for w in [self.seconds,self.duration]:w.setRange(0,1e9);w.setDecimals(9)
        self.absolute_start=Q.QLineEdit();self.absolute_start.setPlaceholderText('ISO timestamp on the native clock')
        self.coord_low=Q.QDoubleSpinBox();self.coord_high=Q.QDoubleSpinBox();self.coord_trace=Q.QDoubleSpinBox()
        for w in [self.coord_low,self.coord_high,self.coord_trace]:w.setRange(-1e9,1e9);w.setDecimals(6)
        for label,w in [('Start time (s)',self.seconds),('Duration (s)',self.duration),('Section start',self.coord_low),('Section end',self.coord_high),('Trace position',self.coord_trace)]:self.basic.addRow(label,w)
        apply_time=Q.QHBoxLayout()
        for label,fn in [('Apply seconds',self.apply_seconds),('Apply timestamp',self.apply_absolute)]:
            button=Q.QPushButton(label);button.clicked.connect(fn);apply_time.addWidget(button)
        self.basic.addRow(apply_time)
        button=Q.QPushButton('Apply section');button.clicked.connect(self.apply_coordinates);self.basic.addRow(button)
        self.review_note=Q.QLabel('Event interpretation: unreviewed selection');self.review_note.setWordWrap(True);lv.addWidget(self.review_note)
        advanced=Q.QWidget();form=Q.QFormLayout(advanced);toggle=Q.QPushButton('Advanced settings');toggle.setCheckable(True);toggle.toggled.connect(advanced.setVisible);lv.addWidget(toggle);lv.addWidget(advanced);advanced.setVisible(False)
        form.setFieldGrowthPolicy(Q.QFormLayout.AllNonFixedFieldsGrow)
        self.group=Q.QComboBox();self.coordinate=Q.QComboBox();self.coordinate.setMinimumWidth(220);self.absolute_start.setMinimumWidth(240);self.id_kind=Q.QComboBox();self.channels=Q.QLineEdit('0:1');self.trace=Q.QSpinBox();self.trace.setMaximum(10**7)
        self.start=Q.QSpinBox();self.end=Q.QSpinBox()
        for w in [self.start,self.end]:w.setMaximum(2_000_000_000)
        self.window=Q.QDoubleSpinBox();self.window.setRange(.00001,10000);self.window.setDecimals(5);self.window.setValue(1)
        self.hop=Q.QDoubleSpinBox();self.hop.setRange(.00001,10000);self.hop.setDecimals(5);self.hop.setValue(.1)
        self.fft=Q.QSpinBox();self.fft.setRange(0,10**7);self.fft.setSpecialValueText('Use window N')
        self.fmax=Q.QDoubleSpinBox();self.fmax.setRange(.001,10**6);self.fmax.setDecimals(3)
        self.bin=Q.QSpinBox();self.bin.setRange(1,8192);self.bin.setValue(10);self.stride=Q.QSpinBox();self.stride.setRange(1,100000);self.stride.setValue(8)
        self.level=Q.QComboBox();self.level.addItems(['Off','0.1','1','5'])
        self.aligned=Q.QCheckBox('Include associated signals for this interval')
        self.compare=Q.QCheckBox('Compare with a 4 s window')
        self.title=Q.QLineEdit('Native recording · selected section')
        for label,w in [('Channel identifier',self.id_kind),('Channels (a:b exclusive or comma IDs)',self.channels),('Trace (array index)',self.trace),('Start sample (inclusive)',self.start),('End sample (exclusive)',self.end),('FFT samples',self.fft),('Waterfall sample bin',self.bin),('Waterfall channel stride',self.stride),('Received level window (s)',self.level),('Figure title',self.title)]:form.addRow(label,w)
        self.basic.insertRow(0,'Signal group',self.group);self.basic.insertRow(1,'Coordinate',self.coordinate)
        self.basic.addRow(Q.QLabel('<b>Analysis settings</b>'));self.basic.addRow('Window duration (s)',self.window);self.basic.addRow('Time step (s)',self.hop);self.basic.addRow('Maximum frequency (Hz)',self.fmax);self.basic.addRow(self.aligned);self.basic.addRow(self.compare)
        row=Q.QVBoxLayout();lv.addLayout(row)
        for label,slot in [('Preview',self.preview),('Review analysis settings',self.show_plan),('Use suggested display bin',self.accept_bin)]:
            b=Q.QPushButton(label);b.clicked.connect(slot);row.addWidget(b)
        self.plan_note=Q.QPlainTextEdit();self.plan_note.setReadOnly(True);self.plan_note.setMinimumHeight(60);self.plan_note.setMaximumHeight(220);lv.addWidget(self.plan_note);self.plan_details={};button=Q.QPushButton('Show analysis details');button.clicked.connect(lambda:self.show_details('Analysis details',self.plan_details));lv.addWidget(button)
        row=Q.QGridLayout();outer.addLayout(row)
        self.analyse_button=Q.QPushButton('Run analysis…');self.analyse_button.clicked.connect(self.choose_analysis);row.addWidget(self.analyse_button,0,0)
        self.validate_button=Q.QPushButton('Numerical verification');self.validate_button.clicked.connect(self.validate_result);row.addWidget(self.validate_button,0,1)
        self.export_button=Q.QPushButton('Export figure and data…');self.export_button.clicked.connect(self.choose_export);row.addWidget(self.export_button,1,0,1,2)
        self.presentation=Q.QComboBox();self.presentation.addItem('Overview','archive');self.presentation.addItem('Spectrogram, waveform and PSD (18 cm)','marine');self.presentation.addItem('Aligned signals (18 cm)','aligned');self.presentation.addItem('Window comparison (18 cm)','resolution');self.presentation.addItem('Waterfall, spectrogram and PSD (18 cm)','spatial');outer.addWidget(self.presentation)
        for key in ['aligned','resolution']:
            item=self.presentation.model().item(self.presentation.findData(key));item.setEnabled(False);item.setToolTip('Open a compatible saved result to use this layout')
        self.stop_button=Q.QPushButton('Stop');self.stop_button.clicked.connect(self.stop);self.stop_button.setEnabled(False);outer.addWidget(self.stop_button)
        self.tabs=Q.QTabWidget();splitter.addWidget(self.tabs);self.metadata=Q.QPlainTextEdit();self.metadata.setReadOnly(True);self.summary=Q.QTextBrowser();metadata_tabs=Q.QTabWidget();metadata_tabs.addTab(self.summary,'Summary');metadata_tabs.addTab(self.metadata,'Detailed metadata');self.tabs.addTab(metadata_tabs,'Recording information')
        self.plot_holder=Q.QWidget();self.plot_layout=Q.QVBoxLayout(self.plot_holder);self.tabs.addTab(self.plot_holder,'Preview and results')
        self.evidence_view=Q.QTextBrowser();self.evidence_view.setHtml(verification_html({'status':'not supplied'}));ev=Q.QWidget();evl=Q.QVBoxLayout(ev);evl.addWidget(self.evidence_view);self.evidence_details={};b=Q.QPushButton('Show verification details');b.clicked.connect(lambda:self.show_details('Verification details',self.evidence_details));evl.insertWidget(0,b);self.tabs.addTab(ev,'Verification')
        splitter.setSizes([510,840]);self.status=Q.QLabel('Ready');self.status.setWordWrap(True);v.addWidget(self.status);diag=Q.QHBoxLayout();v.addLayout(diag);self.detail_button=Q.QPushButton('Error details');self.detail_button.clicked.connect(lambda:self.show_details('Diagnostic report',self.last_error or 'No error recorded'));diag.addWidget(self.detail_button);self.detail_button.hide()
        self.group.currentIndexChanged.connect(self.select_group);self.coordinate.currentIndexChanged.connect(self.sync_coordinate_fields);
        for w in [self.start,self.end]:w.valueChanged.connect(self.sync_time_fields)
        for w in [self.start,self.end,self.trace]:w.valueChanged.connect(self.update_review)
        self.channels.textChanged.connect(self.update_review);self.coordinate.currentIndexChanged.connect(self.update_review)
        self.seconds.setToolTip('Time relative to recording start');self.hop.setToolTip('Interval between spectral window starts; accepted samples shown in analysis settings');self.aligned.setToolTip('Available for the compatible three-signal HDF5 profile: DAS, received and nominal source signals. Alignment uses recorded timestamps; external synchronization is not established.');self.coord_low.setToolTip('Inclusive coordinate bound in the selected origin and unit');self.coord_high.setToolTip('Inclusive coordinate bound in the selected origin and unit');self.basic.insertRow(4,Q.QLabel('Recording timestamp'));self.basic.insertRow(5,self.absolute_start);self.clock_note=Q.QLabel('');self.clock_note.setWordWrap(True);self.clock_note.setMaximumWidth(450);self.clock_note.setMinimumHeight(64);self.basic.insertRow(6,self.clock_note);self.timer=QtCore.QTimer(self);self.timer.setInterval(30);self.timer.timeout.connect(self.poll);self.timer.start()

    def update_review(self):
        if not self.signals:return
        try:self.review_note.setText('Event interpretation: '+self.recipe()['event_review_status'])
        except Exception:self.review_note.setText('Event interpretation: selection incomplete')

    def show_details(self,title,value):
        dialog=Q.QDialog(self);dialog.setWindowTitle(title);dialog.resize(780,560);layout=Q.QVBoxLayout(dialog)
        view=Q.QPlainTextEdit();view.setReadOnly(True);content=value if isinstance(value,str) else json.dumps(value,indent=2);view.setPlainText(content);layout.addWidget(view)
        save=Q.QPushButton('Save diagnostic report…');layout.addWidget(save)
        def write():
            path,_=Q.QFileDialog.getSaveFileName(dialog,'Save diagnostic report','','Text (*.txt)')
            if path:Path(path).write_text(content)
        save.clicked.connect(write);dialog.exec()
    def report_error(self,error):
        self.last_error=str(error);self.status.setText('Error: '+concise_error(error));self.detail_button.show()
    def guard(self,fn):
        try:return fn()
        except Exception as e:self.report_error(traceback.format_exc());return None
    def choose_recording(self):
        path,_=Q.QFileDialog.getOpenFileName(self,'Native recording','','Native recordings (*.h5 *.hdf5 *.mat)')
        if path:self.guard(lambda:self.open_recording(path))
    def choose_folder(self):
        folder=Q.QFileDialog.getExistingDirectory(self,'Select recording folder')
        if not folder:return
        p=Path(folder);files=sorted(x for x in p.rglob('*') if x.suffix.lower() in ('.h5','.hdf5','.mat') and not x.name.startswith('._') and '_acquisition' not in x.parts)
        if len(files)>100: self.status.setText('This folder contains more than 100 recordings. Choose a smaller folder or open one recording.');return
        chosen,ok=Q.QInputDialog.getItem(self,'Native recordings','Choose recording',[str(x.relative_to(p)) for x in files],0,False)
        if ok:self.guard(lambda:self.open_recording(p/chosen))
    def open_recording(self,path):
        t=time.perf_counter();self.signals=open_recording(path);self.path=str(Path(path).resolve());self.recipe_base={};self.title.setText('Native recording · selected section');self.group.blockSignals(True);self.group.clear();self.group.addItems([s.group for s in self.signals]);self.group.blockSignals(False);self.select_group();self.timings['metadata_open_s']=time.perf_counter()-t;self.history.append('open / inspect');self.tabs.setCurrentIndex(0)
    def select_group(self):
        if not self.signals:return
        s=self.signals[self.group.currentIndex()];self.name.ensurePolished();self.name.setText(self.name.fontMetrics().elidedText(Path(self.path).name,QtCore.Qt.ElideMiddle,430));self.name.setToolTip(Path(self.path).name);self.clock_note.setText(s.clock);self.coord_trace.setToolTip(self.coordinate.currentText())
        self.metadata.setPlainText(json.dumps(s.summary(),indent=2));self.coordinate.clear();self.coordinate.addItems(list(s.coordinates));self.id_kind.clear();self.id_kind.addItems(['array index',*s.channels]);self.channels.setText(f'0:{s.c}');self.trace.setMaximum(s.c-1);self.trace.setValue(s.c//2);self.start.setValue(0);self.end.setValue(s.n);self.fmax.setValue(np.floor((s.fs/2)*1000)/1000);self.level.setEnabled(s.pressure);self.level.setCurrentIndex(2 if s.pressure else 0);self.aligned.setEnabled(s.group=='/DAS' and {v.group for v in self.signals}=={'/DAS','/Recorder-C','/Source'} and all(v.profile=='lake-hdf5-2' for v in self.signals));self.aligned.setChecked(False);self.compare.setChecked(False);self.sync_time_fields();self.sync_coordinate_fields();self.show_summary(s)
    def show_summary(self,s):
        import html
        rows=[('Format',FORMAT_NAMES.get(s.profile,'Supported recording')),('Interpretation state',s.metadata.get('interpretation_state','unresolved')),('Quantity',s.quantity),('Stored unit',s.unit),('Interpretation',s.calibration),('Timing',f'{s.fs:.9g} Hz; {s.n*s.dt:.9g} s; {s.clock}'),('Origin',s.origin),('Channels',f'{s.c}; coordinate choices retain their own origins'),('Pressure levels','Eligible received pressure' if s.pressure else 'Disabled: received-pressure interpretation not established'),('Findings',s.metadata.get('findings',s.metadata.get('coordinate_finding','See native metadata'))),('Integrity',s.source['integrity'])]
        self.summary.setHtml('<h3>Recording summary</h3>'+''.join('<p><b>'+html.escape(k)+'</b><br>'+html.escape(str(v))+'</p>' for k,v in rows))
    def sync_time_fields(self):
        if not self.signals:return
        s=self.signals[self.group.currentIndex()]
        self.seconds.setValue(self.start.value()*s.dt);self.duration.setValue(max(0,self.end.value()-self.start.value())*s.dt)
        self.time_display=time_snapshot(s,self.start.value(),self.end.value())
        self.duration.setValue(float(self.time_display['duration_display']))
        self.absolute_start.setText(self.time_display['timestamp'])
        self.absolute_start.setToolTip('Native clock; nearest nanosecond display. Unchanged display preserves accepted sample bounds. Edited times use half-open bounds without snapping.')
        self.duration.setToolTip('Accepted native duration: '+self.time_display['duration_exact']+' s; display rounded to 9 decimals')
    def sync_coordinate_fields(self):
        if not self.signals:return
        s=self.signals[self.group.currentIndex()];key=self.coordinate.currentText()
        if key not in s.coordinates:return
        v=s.coordinates[key];self.coord_low.setValue(float(np.min(v)));self.coord_high.setValue(float(np.max(v)));self.coord_trace.setValue(float(v[min(self.trace.value(),s.c-1)]))
    def apply_seconds(self):
        def run():
            s=self.signals[self.group.currentIndex()];a,b=interval_indices(s,self.seconds.value(),self.duration.value(),resolution_s=1e-9);self.start.setValue(a);self.end.setValue(b);self.show_plan()
        return self.guard(run)
    def apply_absolute(self):
        def run():
            s=self.signals[self.group.currentIndex()]
            a,b=absolute_interval(s,self.absolute_start.text(),str(self.duration.value()),self.time_display)
            # Validate both bounds before an atomic commit; errors preserve the last selection.
            for w in [self.start,self.end]:w.blockSignals(True)
            self.start.setValue(a);self.end.setValue(b)
            for w in [self.start,self.end]:w.blockSignals(False)
            self.sync_time_fields();self.update_review();self.last_error=None;self.show_plan()
            self.status.setText(f'Accepted samples [{a}, {b}); exact native duration '+self.time_display['duration_exact']+' s; '+s.clock)
            self.history.append('apply native-clock timestamp / accepted sample bounds')
        return self.guard(run)
    def apply_coordinates(self):
        def run():
            s=self.signals[self.group.currentIndex()];ch,tr=coordinate_indices(s,self.coordinate.currentText(),self.coord_low.value(),self.coord_high.value(),self.coord_trace.value(),resolution=1e-6);self.id_kind.setCurrentText('array index');self.channels.setText(','.join(map(str,ch)));self.trace.setValue(tr);self.show_plan()
        return self.guard(run)
    def indices(self):
        s=self.signals[self.group.currentIndex()];txt=self.channels.text().strip()
        if ':' in txt:
            parts=[int(z) for z in txt.split(':')];ids=np.arange(*parts)
        else:ids=np.array([int(z.strip()) for z in txt.split(',')])
        if self.id_kind.currentText()=='array index':return ids.tolist()
        native=s.channels[self.id_kind.currentText()];lookup={int(v):i for i,v in enumerate(native)}
        return sorted(lookup[int(v)] for v in ids)
    def recipe(self):
        s=self.signals[self.group.currentIndex()];spec=dict(group=s.group,start=self.start.value(),end=self.end.value(),channels=self.indices(),trace=self.trace.value(),coordinate=self.coordinate.currentText(),window_s=self.window.value(),hop_s=self.hop.value(),nfft=self.fft.value() or None,fmax=self.fmax.value(),time_bin=self.bin.value(),display_stride=self.stride.value())
        if s.pressure and self.level.currentText()!='Off':spec['pressure_window_s']=float(self.level.currentText())
        # Preserve explicitly loaded display references and review notes, not old selection values.
        if self.recipe_base.get('signals'):
            for k in ['spectral_db_limits']:
                if k in self.recipe_base['signals'][0]:spec[k]=self.recipe_base['signals'][0][k]
        r=dict(algorithm='public-native-analysis-1',backend='cpu-float64',title=self.title.text(),subtitle=self.recipe_base.get('subtitle',s.quantity+' ['+s.unit+']'),event_review=self.recipe_base.get('event_review','Local event interpretation not supplied'),signals=[spec],layout='case')
        if self.aligned.isChecked():
            from datetime import datetime
            r['layout']='aligned'
            for other in self.signals:
                if other.group==s.group:continue
                shift=(datetime.fromisoformat(s.origin.replace('Z','+00:00'))-datetime.fromisoformat(other.origin.replace('Z','+00:00'))).total_seconds()
                start=accepted_samples(shift+spec['start']*s.dt,other.fs);end=accepted_samples(shift+spec['end']*s.dt,other.fs)
                old=next((v for v in self.recipe_base.get('signals',[]) if v['group']==other.group),{})
                r['signals'].append(dict(group=other.group,start=start,end=end,channels=list(range(other.c)),trace=old.get('trace',0),coordinate=next(iter(other.coordinates)),window_s=old.get('window_s',.1),hop_s=old.get('hop_s',.05),nfft=old.get('nfft'),fmax=min(spec['fmax'],other.fs/2),time_bin=old.get('time_bin',500),display_stride=1,**({'pressure_window_s':old.get('pressure_window_s',1)} if other.pressure else {})))
        if self.compare.isChecked():
            r['layout']='comparison';second=dict(spec,window_s=4,nfft=None);r['signals'].append(second)
        r['event_review_scope']=self.recipe_base.get('event_review_scope')
        r=qualify_review(r,s.source)
        if r['event_review_status']=='unreviewed selection':
            r['subtitle']=s.quantity+' ['+s.unit+']'
            if r['title']==self.recipe_base.get('title'):r['title']='Native recording | unreviewed selection'
        return r
    def apply_recipe(self,recipe):
        self.recipe_base=recipe;spec=recipe['signals'][0];self.group.setCurrentText(spec['group']);self.recipe_base=recipe
        self.id_kind.setCurrentText('array index');self.coordinate.setCurrentText(spec['coordinate']);ch=spec['channels'];self.channels.setText(f'{ch[0]}:{ch[-1]+1}' if ch==list(range(ch[0],ch[-1]+1)) else ','.join(map(str,ch)))
        for w,k in [(self.start,'start'),(self.end,'end'),(self.trace,'trace'),(self.window,'window_s'),(self.hop,'hop_s'),(self.fmax,'fmax'),(self.bin,'time_bin'),(self.stride,'display_stride')]:w.setValue(spec[k])
        self.fft.setValue(spec.get('nfft') or 0);self.title.setText(recipe['title']);self.aligned.setChecked(recipe.get('layout')=='aligned');self.compare.setChecked(recipe.get('layout')=='comparison');self.level.setCurrentText(str(spec.get('pressure_window_s','Off')));self.sync_time_fields();self.sync_coordinate_fields();v=self.signals[self.group.currentIndex()].coordinates[self.coordinate.currentText()];self.coord_low.setValue(float(np.min(v[ch])));self.coord_high.setValue(float(np.max(v[ch])));self.coord_trace.setValue(float(v[spec['trace']]));self.history.append('select / recipe loaded');self.show_plan();self.update_review()
    def choose_recipe(self):
        f,_=Q.QFileDialog.getOpenFileName(self,'Load processing settings','','JSON (*.json)')
        if f:self.guard(lambda:self.apply_recipe(json.loads(Path(f).read_text())))
    def save_recipe(self):
        f,_=Q.QFileDialog.getSaveFileName(self,'Save settings','','JSON (*.json)')
        if f:self.guard(lambda:dump(f,self.recipe()))
    def show_plan(self):
        def run():
            r=self.recipe();p=plan(self.signals,r);self.plan_details=p;self.plan_note.setPlainText(plan_text(p,self.signals,r));self.history.append('inspect plan');return p
        return self.guard(run)
    def accept_bin(self):
        p=self.show_plan()
        if p:self.bin.setValue(p['signals'][0]['suggested_time_bin']);self.show_plan();self.history.append('explicit display bin acceptance')
    def set_figure(self,fig):
        while self.plot_layout.count():
            item=self.plot_layout.takeAt(0);w=item.widget()
            if w:w.hide();w.deleteLater()
        self.canvas=FigureCanvasQTAgg(fig);self.plot_layout.addWidget(NavigationToolbar2QT(self.canvas,self));self.plot_layout.addWidget(self.canvas);self.canvas.draw();self.tabs.setCurrentIndex(1)
    def preview(self):
        def run():
            t=time.perf_counter();s=self.signals[self.group.currentIndex()];a=self.start.value();b=min(self.end.value(),a+8192);ch=self.trace.value();y=s.read(a,b,[ch])[:,0]
            fig=Figure(figsize=(8,5),layout='constrained');ax=fig.add_subplot();ax.plot(s.times(a,b),y,lw=.7);ax.set(title=f'Native preview: {s.group}, index{ch}; first {b-a} requested samples',xlabel='Time from recording origin (s)',ylabel=s.unit);self.set_figure(fig);self.timings['preview_s']=time.perf_counter()-t;self.history.append('native preview');self.status.setText(f'Preview uses [{a}, {b}); source dtype {s.dtype}; analysis selection unchanged')
        return self.guard(run)
    def choose_analysis(self):
        d=Q.QFileDialog.getSaveFileName(self,'New result folder name')[0]
        if d:self.guard(lambda:self.start_analysis(d))
    def start_analysis(self,destination):
        r=self.recipe();p=plan(self.signals,r)
        if not p['allowed']:raise ValueError('; '.join(p['blocking_reasons']))
        self.start_worker(dict(kind='analyse',path=self.path,recipe=r,output=str(destination)))
    def validate_result(self):
        if self.path and self.result_directory:self.start_worker(dict(kind='validate',path=self.path,directory=self.result_directory))
        else:self.status.setText('Open/relink the matching native recording and result first')
    def choose_export(self):
        d=Q.QFileDialog.getSaveFileName(self,'New figure bundle folder name')[0]
        if d:self.guard(lambda:self.start_export(d))
    def start_export(self,destination):self.start_worker(dict(kind='export',directory=self.result_directory,output=str(destination),presentation=dict(profile=self.presentation.currentData(),version='publication-layout-1',width_cm=18 if self.presentation.currentData()!='archive' else 25)))
    def start_worker(self,request):
        if self.process is not None:raise ValueError('Wait for current worker')
        ctx=mp.get_context('spawn');self.q=ctx.Queue();self.stop_event=ctx.Event();self.request=request;self.process=ctx.Process(target=public_worker,args=(request,self.q,self.stop_event));self.process.start();self.stopping=False;self.status.setText('Running '+request['kind']);self.stop_button.setEnabled(True);self.last_error=None
        for w in [self.analyse_button,self.validate_button,self.export_button]:w.setEnabled(False)
    def stop(self):
        if self.process:self.stop_event.set();self.stopping=True;self.status.setText('Stopping… waiting for bounded in-flight work');self.status.repaint();self.history.append('Stop acknowledged')
    def poll(self):
        if self.process is None:return
        terminal=False
        while True:
            try:kind,value=self.q.get_nowait()
            except queue.Empty:break
            if kind=='progress':
                if not self.stopping:self.status.setText(value)
            else:
                terminal=True
                if kind=='done':
                    self.timings[value['kind']+'_s']=value['elapsed'];self.history.append(value['kind'])
                    if value['kind']=='analyse':self.open_result(value['output'])
                    elif value['kind']=='validate':self.open_result(self.result_directory)
                    else:self.status.setText('Export complete: '+Path(value['output']).name)
                elif kind=='error':self.report_error(value);self.history.append('error')
                else:self.status.setText(value);self.history.append('interrupted — regenerate')
        if terminal or (not self.process.is_alive() and self.q.empty()):
            self.process.join(.1);self.process=None;self.stop_button.setEnabled(False)
            for w in [self.analyse_button,self.validate_button,self.export_button]:w.setEnabled(True)
    def open_result(self,directory):
        m,r=load_result(directory);self.result_directory=str(directory)
        validation=evidence(directory);statuses=dict(m['statuses'])
        # The creation-time status is immutable provenance; current validation
        # is determined from the bound sidecar each time the result is opened.
        statuses['numerical_validation']=validation['status']
        self.evidence_details=dict(statuses=statuses,independent_validation=validation);self.evidence_view.setHtml(verification_html(validation));self.set_figure(figure(directory));self.status.setText('Result opened · Numerical verification: '+status_name(validation['status']));self.history.append('reopen result')
        for key,allowed,reason in [('aligned',len(m['signals'])==3 and any(x['metadata']['received_pressure'] for x in m['signals']),'Requires three aligned signals and qualified received pressure'),('resolution',len(m['signals'])==2,'Requires the two-window comparison result')]:
            item=self.presentation.model().item(self.presentation.findData(key));item.setEnabled(allowed);item.setToolTip('Available' if allowed else reason)
    def choose_result(self):
        d=Q.QFileDialog.getExistingDirectory(self,'Open saved result or export folder')
        if d:self.guard(lambda:self.open_result(d))
    def closeEvent(self,event):
        if self.process:self.stop();event.ignore()
        else:event.accept()
