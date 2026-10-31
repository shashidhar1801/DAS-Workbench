"""Local Qt application. Scientific work runs in a spawned process."""
from pathlib import Path
from datetime import datetime
import json
import math
import multiprocessing as mp
import queue
import time
import traceback
import uuid
import platform
import importlib.util
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets as Q
import pyqtgraph as pg
from .contracts import (Selection,AnalysisRecipe,iso,utc_us,jsonable,read_json,atomic_json,
                        run_from_dict,recipe_from_dict)
from .project import Project
from .engine import create_job,estimate
from .exporter import display_db
from .worker import worker_main
from .selection import parse_channels
from .overview import resolve_overview, plan_overview, make_overview_figure, STYLE

pg.setConfigOptions(antialias=False,background="w",foreground="#283d48",imageAxisOrder="row-major")


class ExportDialog(Q.QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export figure and data")
        form = Q.QFormLayout(self)
        self.width=Q.QDoubleSpinBox();self.width.setRange(5,100);self.width.setValue(20)
        self.height=Q.QDoubleSpinBox();self.height.setRange(5,100);self.height.setValue(22)
        self.dpi=Q.QSpinBox();self.dpi.setRange(72,1200);self.dpi.setValue(300)
        self.font=Q.QDoubleSpinBox();self.font.setRange(6,24);self.font.setValue(9)
        self.log=Q.QCheckBox("Logarithmic frequency")
        self.vmin=Q.QLineEdit();self.vmin.setPlaceholderText("Automatic")
        self.vmax=Q.QLineEdit();self.vmax.setPlaceholderText("Automatic")
        for label,widget in [("Width (cm)",self.width),("Height (cm)",self.height),("DPI",self.dpi),
                              ("Font size (pt)",self.font),("Frequency",self.log),("Colour minimum (dB)",self.vmin),("Colour maximum (dB)",self.vmax)]:
            form.addRow(label,widget)
        info=Q.QLabel("Includes PNG, PDF and SVG, full numeric HDF5, exact settings,\nprovenance, descriptive statistics and a processing caption.")
        info.setWordWrap(True);form.addRow(info)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.Ok|Q.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);form.addRow(buttons)

    def settings(self):
        return dict(width_cm=self.width.value(),height_cm=self.height.value(),dpi=self.dpi.value(),font_size=self.font.value(),
                    log_frequency=self.log.isChecked(),vmin_db=float(self.vmin.text()) if self.vmin.text() else None,
                    vmax_db=float(self.vmax.text()) if self.vmax.text() else None)


class OverviewDialog(Q.QDialog):
    def __init__(self,run,start_us,end_us,parent=None):
        super().__init__(parent)
        self.setWindowTitle("Full-fibre overview")
        self.run,self.start_us,self.end_us=run,start_us,end_us
        form=Q.QFormLayout(self)
        info=Q.QLabel("Uses the selected UTC interval and every fibre channel.\nMean-removed phase in radians; no filtering.\nThe trace and RMS use native samples; reduction below affects only the waterfall.")
        info.setWordWrap(True);form.addRow(info)
        self.title=Q.QLineEdit("DAS | full-fibre overview")
        self.trace=Q.QSpinBox();self.trace.setRange(0,run.first.channels-1)
        spacing=run.first.spacing_m or 1
        self.trace.setValue(max(0,min(run.first.channels-1,round(30000/spacing-(run.first.start_locus or 0)))))
        self.distance=Q.QLabel()
        self.stride=Q.QSpinBox();self.stride.setRange(1,run.first.channels);self.stride.setValue(min(16,run.first.channels))
        self.bin=Q.QSpinBox();self.bin.setRange(1,1_000_000);self.bin.setValue(10)
        self.percentile=Q.QDoubleSpinBox();self.percentile.setRange(.01,100);self.percentile.setValue(95)
        self.limit=Q.QLineEdit();self.limit.setPlaceholderText("Automatic percentile of |displayed phase|")
        self.strict=Q.QCheckBox("Exclude all status warnings");self.log=Q.QCheckBox("Logarithmic RMS axis");self.log.setChecked(True)
        for label,w in [("Figure title",self.title),("Trace channel (zero based)",self.trace),("Relative distance",self.distance),
                        ("Waterfall channel stride",self.stride),("Waterfall time bin (samples)",self.bin),
                        ("Absolute-value colour percentile",self.percentile),("Fixed ± colour limit (rad)",self.limit)]:form.addRow(label,w)
        form.addRow(self.strict);form.addRow(self.log)
        self.info=Q.QLabel();self.info.setWordWrap(True);form.addRow(self.info)
        self.planning_decision=None
        self.suggest=Q.QPushButton("Accept suggested display bin");self.suggest.clicked.connect(self.accept_suggested_bin);form.addRow(self.suggest)
        self.buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.Ok|Q.QDialogButtonBox.Cancel)
        self.buttons.button(Q.QDialogButtonBox.Ok).setText("Generate overview")
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject);form.addRow(self.buttons)
        for w in (self.trace,self.stride,self.bin,self.percentile):w.valueChanged.connect(self.update_estimate)
        self.limit.textChanged.connect(self.update_estimate);self.update_estimate()

    def settings(self):
        return dict(title=self.title.text(),trace_channel=self.trace.value(),channel_stride=self.stride.value(),
            time_bin_samples=self.bin.value(),clip_percentile=self.percentile.value(),
            colour_limit=float(self.limit.text()) if self.limit.text() else None,
            strict_quality=self.strict.isChecked(),rms_log=self.log.isChecked())

    def accept_suggested_bin(self):
        requested=self.settings()
        _,_,plan=plan_overview(self.run,self.start_us,self.end_us,requested)
        suggestion=plan['suggested_bin_samples']
        if suggestion is None:return
        self.bin.setValue(suggestion)
        self.planning_decision=dict(action="explicitly accepted suggested display bin",requested=requested,accepted=self.settings())
        self.update_estimate()

    def update_estimate(self):
        self.suggest.setEnabled(False)
        try:
            settings,selected,e=plan_overview(self.run,self.start_us,self.end_us,self.settings())
            self.distance.setText(f"{(self.trace.value()+self.run.first.start_locus)*self.run.first.spacing_m/1000:.6f} km")
            text=(f"{e['all_channels']:,} channels × {selected['count']:,} native samples; trace {e['native_trace_samples']:,} samples.\n"
                f"Waterfall: {e['waterfall_rows']:,} × {e['waterfall_columns']:,} = {e['waterfall_pixels']:,} pixels. Bin {e['bin_samples']} samples ({e['bin_seconds']:g} s); final bin {e['final_bin_samples']} ({e['final_bin_seconds']:g} s).\n"
                f"Logical int32 input {e['logical_read_bytes']/1e9:.3f} GB; compressed disk reads unknown.\n"
                f"Numeric storage estimate {e['numeric_bytes_upper']/1e6:.1f} MB; staging {e['temporary_disk_bytes_estimate']/1e6:.1f} MB; working buffers {e['working_buffer_bytes_estimate']/1e6:.1f} MB.\n"
                "Buffer estimate excludes HDF5/OS cache, Python, GUI and export. CPU float64; ≤510-channel bounded reads.\n"
                "Limits: 10,000,000 native trace samples; 8,000,000 waterfall pixels.\n"
                "Stop discards uncommitted work after the in-flight read/compute finishes; regenerate from the beginning.")
            if e['blocking_reasons']:text+='\n'+'\n'.join(e['blocking_reasons'])
            self.suggest.setEnabled(e['suggested_bin_samples'] is not None)
            if e['suggested_bin_samples']:
                self.suggest.setText(f"Accept bin {e['bin_samples']} → {e['suggested_bin_samples']} samples ({e['bin_seconds']:g} → {e['suggested_bin_samples']/float(self.run.first.rate):g} s); keep stride")
            if self.planning_decision:
                old=self.planning_decision['requested']['time_bin_samples']
                text+=f"\nExplicitly accepted: requested {old} samples ({old/float(self.run.first.rate):g} s) → {settings['time_bin_samples']} samples ({e['bin_seconds']:g} s)."
            self.info.setText(text)
            self.buttons.button(Q.QDialogButtonBox.Ok).setEnabled(e['allowed'])
        except Exception as exc:
            self.info.setText(str(exc));self.buttons.button(Q.QDialogButtonBox.Ok).setEnabled(False)


class OverviewExportDialog(Q.QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle("Export overview")
        form=Q.QFormLayout(self);self.fields={}
        for key,label,lo,hi in [("width_cm","Width (cm)",18,40),("height_cm","Height (cm)",14,32),
                              ("dpi","DPI",72,600),("font_size","Font size (pt)",7,14)]:
            w=Q.QDoubleSpinBox();w.setRange(lo,hi);w.setValue(STYLE[key]);self.fields[key]=w;form.addRow(label,w)
        info=Q.QLabel("Exports PNG, PDF and SVG, numerical HDF5, spatial RMS CSV,\ncaption, exact recipe, source provenance and checksums.\nUse the navigation toolbar to inspect the preview; export retains the full extent.")
        info.setWordWrap(True);form.addRow(info)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.Ok|Q.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);form.addRow(buttons)

    def settings(self):return {k:w.value() for k,w in self.fields.items()}


class MainWindow(Q.QMainWindow):
    def __init__(self,project=None):
        super().__init__()
        self.setWindowTitle("DAS Workbench")
        self.resize(1480,920)
        self.project_path=None;self.roots={};self.runs=[];self.result_directory=None;self.result=None
        self.process=None;self.messages=None;self.current_task=None;self.pending=[];self.pins=[]
        self.last_error=None
        self.preview=None;self.job_items={};self._loading=False;self.selection_region=None
        self.overview_directory=None;self.overview_canvas=None;self.overview_toolbar=None;self.overview_scroll=None
        self._build()
        self.poller=QtCore.QTimer(self);self.poller.setInterval(40);self.poller.timeout.connect(self.poll);self.poller.start()
        if project: self.load_project(project)

    def open_public_workspace(self):
        from .public_ui import PublicWindow
        self.public_window=PublicWindow(self)
        self.public_window.show()

    def _build(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {font-family: 'Arial'; font-size: 12px;}
            QMainWindow {background:#f5f8fa;}
            QPushButton {padding:7px 10px; border:1px solid #b9cbd3; border-radius:5px; background:#ffffff;}
            QPushButton:hover {background:#e5f2f5;}
            QPushButton#primary {background:#126779; color:white; font-weight:600; border:0;}
            QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox {padding:5px; border:1px solid #b9cbd3; border-radius:4px; background:white;}
            QGroupBox {font-weight:bold; border:1px solid #d4e0e5; border-radius:5px; margin-top:10px; padding-top:12px;}
            QGroupBox::title {subcontrol-origin:margin; left:8px;}
            QTabBar::tab {padding:9px 13px;}
            QTabBar::tab:selected {color:#126779; font-weight:bold;}
        """)
        bar=self.addToolBar("Project");bar.setMovable(False)
        public_action=bar.addAction("Recording analysis…")
        public_action.triggered.connect(self.open_public_workspace)
        from .presentation import about_text
        bar.addAction('About').triggered.connect(lambda:Q.QMessageBox.information(self,'About DAS Workbench',about_text()))
        for label,fn in [("New / open project",self.choose_project),("Open file",self.open_file),("Add folder",self.add_folder),
                         ("Refresh",lambda:self.scan()),("Validate time/status",lambda:self.scan(deep=True)),
                         ("Relink sources",self.relink),("Synthetic demo",self.demo),("Quick start",self.help),
                         ("Diagnostics",self.diagnostics)]:
            action=bar.addAction(label);action.triggered.connect(fn)
        self.error_details_action=bar.addAction("Error details…")
        self.error_details_action.setEnabled(False)
        self.error_details_action.setToolTip("No error diagnostic has been recorded.")
        self.error_details_action.triggered.connect(self.show_error_details)
        central=Q.QWidget();self.setCentralWidget(central);outer=Q.QVBoxLayout(central);outer.setContentsMargins(12,10,12,8)
        self.header=Q.QLabel("DAS WORKBENCH   /   Local phase analysis")
        self.header.setStyleSheet("font-size:21px;font-weight:600;color:#174957;padding:4px 0;");outer.addWidget(self.header)
        self.subtitle=Q.QLabel("Open a recording or use the synthetic demo. Source recordings are accessed read-only.")
        self.subtitle.setWordWrap(True);outer.addWidget(self.subtitle)
        split=Q.QSplitter();outer.addWidget(split,1)
        left=Q.QWidget();ll=Q.QVBoxLayout(left);ll.setContentsMargins(0,0,5,0)
        ll.addWidget(Q.QLabel("RECORDINGS / CONTINUOUS RUNS"))
        self.tree=Q.QTreeWidget();self.tree.setHeaderLabels(["Acquisition / file","Rate / samples"])
        self.tree.header().setSectionResizeMode(0,Q.QHeaderView.Stretch);self.tree.header().setSectionResizeMode(1,Q.QHeaderView.ResizeToContents)
        self.tree.setMinimumWidth(250);self.tree.itemSelectionChanged.connect(self.run_selected);ll.addWidget(self.tree,3)
        ll.addWidget(Q.QLabel("SAVED JOBS & RESULTS"))
        self.jobs=Q.QListWidget();self.jobs.itemDoubleClicked.connect(self.open_saved_job);ll.addWidget(self.jobs,2)
        row=Q.QHBoxLayout();resume=Q.QPushButton("Resume selected");resume.clicked.connect(self.resume_selected);row.addWidget(resume)
        browse=Q.QPushButton("Open result…");browse.clicked.connect(self.browse_result);row.addWidget(browse);ll.addLayout(row)
        split.addWidget(left)
        middle=Q.QWidget();ml=Q.QVBoxLayout(middle);ml.setContentsMargins(3,0,3,0)
        self.snapshot=Q.QLabel("No calculated result");self.snapshot.setWordWrap(True);self.snapshot.setStyleSheet("background:#eaf2f5;padding:8px;");ml.addWidget(self.snapshot)
        self.tabs=Q.QTabWidget();ml.addWidget(self.tabs,1)
        self.wave=pg.PlotWidget(title="Waveform · min/max envelope");self.wave.setLabel("bottom","Elapsed time","s")
        self.tabs.addTab(self.wave,"Waveform")
        self.psd=pg.PlotWidget(title="Welch PSD");self.psd.setLabel("bottom","Frequency","Hz");self.psd.showGrid(x=True,y=True,alpha=.18)
        self.tabs.addTab(self.psd,"PSD")
        self.spec=pg.PlotWidget(title="Spectrogram · no time averaging");self.spec.setLabel("bottom","Elapsed time","s");self.spec.setLabel("left","Frequency","Hz")
        self.spec_bar=None
        self.tabs.addTab(self.spec,"Spectrogram")
        self.levels=pg.PlotWidget(title="Waveform RMS");self.levels.setLabel("bottom","Elapsed time","s");self.tabs.addTab(self.levels,"Levels")
        self.overview=pg.PlotWidget(title="Time–channel overview · explicitly reduced display");self.overview.setLabel("bottom","Elapsed time","s");self.overview.setLabel("left","Selected channel row")
        self.tabs.addTab(self.overview,"Channel preview")
        self.fibre_tab=Q.QWidget();self.fibre_layout=Q.QVBoxLayout(self.fibre_tab)
        fibre_actions=Q.QHBoxLayout()
        self.fibre_generate=Q.QPushButton("Generate full-fibre overview…");self.fibre_generate.clicked.connect(self.full_fibre_overview)
        self.fibre_export=Q.QPushButton("Export three-panel figure…");self.fibre_export.clicked.connect(self.export_fibre_overview)
        self.fibre_export.setEnabled(False)
        fibre_actions.addWidget(self.fibre_generate);fibre_actions.addWidget(self.fibre_export);self.fibre_layout.addLayout(fibre_actions)
        self.fibre_note=Q.QLabel("A: full-fibre waterfall. B: mean-removed native waveform. C: RMS versus distance.\nSelect a recording and UTC interval, then Generate. All fibre channels are included.")
        self.fibre_note.setWordWrap(True);self.fibre_layout.addWidget(self.fibre_note)
        self.tabs.addTab(self.fibre_tab,"Fibre overview")
        self.details=Q.QPlainTextEdit();self.details.setReadOnly(True);self.tabs.addTab(self.details,"Metadata / quality")
        self.statistics=Q.QPlainTextEdit();self.statistics.setReadOnly(True);self.tabs.addTab(self.statistics,"Statistics")
        self.comparisons=pg.PlotWidget(title="Spectral comparison");self.comparisons.addLegend();self.comparisons.setLabel("bottom","Frequency","Hz")
        self.tabs.addTab(self.comparisons,"Compare")
        self.plot_info=Q.QLabel("Numerical analysis uses native samples. Display reduction is recorded separately.");self.plot_info.setWordWrap(True);ml.addWidget(self.plot_info)
        plotbar=Q.QHBoxLayout();self.result_channel=Q.QComboBox();self.result_channel.currentIndexChanged.connect(self.result_channel_changed)
        plotbar.addWidget(Q.QLabel("Result channel"));plotbar.addWidget(self.result_channel)
        self.pin_button=Q.QPushButton("Pin PSD");self.pin_button.clicked.connect(self.pin_psd);plotbar.addWidget(self.pin_button)
        self.log_frequency=Q.QCheckBox("Log frequency (PSD)");self.log_frequency.toggled.connect(lambda b:self.psd.setLogMode(x=b,y=False));plotbar.addWidget(self.log_frequency)
        self.export_button=Q.QPushButton("Export figure and data…");self.export_button.clicked.connect(self.export);plotbar.addWidget(self.export_button);ml.addLayout(plotbar)
        split.addWidget(middle)
        controls=Q.QWidget();cl=Q.QVBoxLayout(controls);cl.setContentsMargins(8,0,0,0)
        form=Q.QFormLayout();form.setLabelAlignment(QtCore.Qt.AlignLeft)
        self.start=Q.QLineEdit();self.end=Q.QLineEdit();self.channels=Q.QLineEdit("0")
        self.channels.setToolTip("Zero-based channels. 1:21 selects channels 1–20. Commas select individual channels.")
        self.quantity=Q.QComboBox();self.quantity.addItems(["phase","counts"])
        self.window=Q.QLineEdit("4");self.welch_hop=Q.QLineEdit("2");self.stft_hop=Q.QLineEdit("1");self.nfft=Q.QLineEdit();self.nfft.setPlaceholderText("Same as window")
        self.rms_bin=Q.QLineEdit("1");self.detrend=Q.QComboBox();self.detrend.addItems(["constant","none"])
        self.band=Q.QLineEdit("1:20");self.band.setToolTip("One or more Hz bands: 1:20,20:40. Empty means no band summaries.")
        self.backend=Q.QComboBox();self.backend.addItem("CPU · float64","cpu");self.backend.addItem("Auto · conservative","auto")
        if platform.system()=="Darwin" and platform.machine()=="arm64":self.backend.addItem("Apple GPU · CPU audited","mlx")
        if importlib.util.find_spec("cupy") is not None:self.backend.addItem("NVIDIA GPU · CPU audited","cupy")
        self.ac=Q.QCheckBox("AC-RMS: selection-wide mean")
        self.strict=Q.QCheckBox("Exclude all status warnings")
        self.save_spec=Q.QCheckBox("Store spectrogram windows");self.save_spec.setChecked(True)
        for label,w in [("Start UTC",self.start),("End UTC (exclusive)",self.end),("Channels (zero based)",self.channels),
                        ("Quantity",self.quantity),("Spectral window (s)",self.window),("Welch hop (s)",self.welch_hop),
                        ("STFT hop (s)",self.stft_hop),("FFT samples",self.nfft),("Spectral detrend",self.detrend),
                        ("RMS bin (s)",self.rms_bin),("Bands (Hz)",self.band),("Compute backend",self.backend)]:
            if w in (self.start,self.end):form.addRow(Q.QLabel(label));form.addRow(w)
            else:form.addRow(label,w)
            if isinstance(w,Q.QLineEdit):w.textChanged.connect(self.settings_changed)
            else:w.currentTextChanged.connect(self.settings_changed)
        cl.addLayout(form)
        for w in [self.ac,self.strict,self.save_spec]:cl.addWidget(w);w.toggled.connect(self.settings_changed)
        distance=Q.QPushButton("Select relative distance range…");distance.clicked.connect(self.distance_selection);cl.addWidget(distance)
        self.resolution=Q.QLabel("Select an acquisition to resolve samples and frequency spacing.");self.resolution.setWordWrap(True);cl.addWidget(self.resolution)
        self.estimate_label=Q.QLabel();self.estimate_label.setWordWrap(True);cl.addWidget(self.estimate_label)
        self.preview_button=Q.QPushButton("Preview waveform / overview");self.preview_button.clicked.connect(self.preview_selection);cl.addWidget(self.preview_button)
        fibre_button=Q.QPushButton("Full-fibre three-panel overview…");fibre_button.clicked.connect(self.full_fibre_overview);cl.addWidget(fibre_button)
        self.run_button=Q.QPushButton("Run analysis / add to queue");self.run_button.setObjectName("primary");self.run_button.clicked.connect(self.analyse);cl.addWidget(self.run_button)
        recipes=Q.QHBoxLayout();save=Q.QPushButton("Save recipe");save.clicked.connect(self.save_recipe);load=Q.QPushButton("Load recipe");load.clicked.connect(self.load_recipe)
        recipes.addWidget(save);recipes.addWidget(load);cl.addLayout(recipes)
        note=Q.QLabel("Phase supports counts and metadata-scaled radians. Pressure calibration is required for SPL. Channel spacing and gauge length describe different properties.")
        note.setWordWrap(True);note.setStyleSheet("color:#5c6c75;padding-top:7px;");cl.addWidget(note);cl.addStretch(1)
        scroll=Q.QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(controls);scroll.setMinimumWidth(305);split.addWidget(scroll)
        split.setSizes([285,850,325])
        status=Q.QHBoxLayout();self.status=Q.QLabel("Ready");status.addWidget(self.status,1)
        self.progress=Q.QProgressBar();self.progress.setMaximumWidth(260);status.addWidget(self.progress)
        self.pause_button=Q.QPushButton("Pause");self.pause_button.clicked.connect(self.pause);status.addWidget(self.pause_button)
        self.cancel_button=Q.QPushButton("Cancel");self.cancel_button.clicked.connect(self.cancel);status.addWidget(self.cancel_button);outer.addLayout(status)

    def retain_error(self,diagnostic):
        self.last_error=str(diagnostic)
        self.error_details_action.setEnabled(True)
        self.error_details_action.setText("Error details (last error)…")
        self.error_details_action.setToolTip("The last recorded error remains available after later tasks start or complete.")

    def show_error_details(self):
        if self.last_error is None:return
        content=self.last_error
        dialog=Q.QDialog(self);dialog.setWindowTitle("Last error details");dialog.resize(780,560)
        layout=Q.QVBoxLayout(dialog)
        note=Q.QLabel("Last recorded error. This diagnostic is retained even if a later task completes successfully.")
        note.setWordWrap(True);layout.addWidget(note)
        view=Q.QPlainTextEdit();view.setObjectName("error_diagnostic");view.setReadOnly(True);view.setPlainText(content);layout.addWidget(view)
        save=Q.QPushButton("Save diagnostic report…");layout.addWidget(save)
        def write():
            path,_=Q.QFileDialog.getSaveFileName(dialog,"Save diagnostic report","","Text (*.txt)")
            if path:
                try:Path(path).write_text(content,encoding="utf8")
                except Exception as exc:self.error(exc)
        save.clicked.connect(write)
        close=Q.QDialogButtonBox(Q.QDialogButtonBox.Close);close.rejected.connect(dialog.reject);layout.addWidget(close)
        dialog.exec()

    def error(self,message,*,trusted=False):
        if trusted:
            summary=str(message)
        else:
            diagnostic="".join(traceback.format_exception(message)) if isinstance(message,BaseException) else str(message)
            self.retain_error(diagnostic)
            summary="The operation could not be completed. Open Error details for diagnostic information."
        self.status.setText(summary);Q.QMessageBox.warning(self,"DAS Workbench",summary)

    def choose_project(self):
        if self.process: return self.error("Finish or pause the active job before changing projects.",trusted=True)
        path=Q.QFileDialog.getExistingDirectory(self,"Choose an empty folder for a new project, or an existing project")
        if path:self.load_project(path)

    def load_project(self,path):
        try:
            with Project(path) as p:
                self.project_path=str(p.path);self.roots=dict(p.roots);self.runs=p.runs()[0]
                saved=dict(p.document)
            self.subtitle.setText(f"Project: {Path(self.project_path).name} · independent local analysis")
            self.subtitle.setToolTip(self.project_path)
            self.populate_runs();self.refresh_jobs()
            if saved.get("last_controls"):
                self.set_controls(saved["last_controls"])
        except Exception as exc:self.error(exc)

    def ensure_project(self):
        if not self.project_path:self.choose_project()
        return bool(self.project_path)

    def open_file(self):
        if not self.ensure_project():return
        path,_=Q.QFileDialog.getOpenFileName(self,"Open DAS recording",filter="HDF5 (*.h5 *.hdf5)")
        if path:self.scan(paths=[path])

    def add_folder(self):
        if not self.ensure_project():return
        path=Q.QFileDialog.getExistingDirectory(self,"Choose a recording folder")
        if path:self.scan(paths=[path])

    def scan(self,paths=(),deep=False):
        if not self.ensure_project():return
        self.start_worker(dict(task="scan",project=self.project_path,paths=list(paths),deep=deep))

    def populate_runs(self):
        self._loading=True;self.tree.clear()
        for i,run in enumerate(self.runs):
            source=run.first
            name=f"{iso(run.start_us)[:10]} · {len(run.files)} files"
            item=Q.QTreeWidgetItem([name,f"{float(source.rate):g} Hz"]);item.setData(0,QtCore.Qt.UserRole,i)
            item.setToolTip(0,f"{iso(run.start_us)} → {iso(run.end_us)}\n{source.acquisition_id}\n{run.boundary_before}")
            self.tree.addTopLevelItem(item)
            for s in run.files:
                child=Q.QTreeWidgetItem([s.relative_path,f"{s.samples:,}"])
                child.setData(0,QtCore.Qt.UserRole,i);child.setData(0,QtCore.Qt.UserRole+1,s.id);child.setToolTip(0,s.validation);item.addChild(child)
        self._loading=False
        if self.tree.topLevelItemCount():self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def current_run(self):
        item=self.tree.currentItem()
        if not item:raise ValueError("Open or select a recording first.")
        return self.runs[item.data(0,QtCore.Qt.UserRole)]

    def run_selected(self):
        if self._loading:return
        try:
            run=self.current_run();s=run.first;self._loading=True
            selected_id=self.tree.currentItem().data(0,QtCore.Qt.UserRole+1)
            selected_file=next((f for f in run.files if f.id==selected_id),None)
            start=selected_file.first_us if selected_file else run.start_us
            end=selected_file.end_us if selected_file else min(run.end_us,start+60_000_000)
            self.start.setText(iso(start));self.end.setText(iso(end))
            try:parse_channels(self.channels.text(),s.channels)
            except ValueError:self.channels.setText("0")
            self.details.setPlainText(json.dumps(dict(acquisition=s.acquisition_id,files=len(run.files),
                start=iso(run.start_us),end_exclusive=iso(run.end_us),channel_count=s.channels,rate=s.rate,
                gauge_m=s.gauge_m,channel_pitch_m=s.spacing_m,quantity="vendor processed phase",validation=s.validation,
                boundary_before=run.boundary_before,source_metadata=s.metadata),indent=2))
            self.snapshot.setText(f"fs {float(s.rate):g} Hz · {s.channels:,} channels · pitch {s.spacing_m} m · gauge {s.gauge_m} m\n{iso(run.start_us)} to {iso(run.end_us)} · {s.validation}")
        except Exception as exc:self.status.setText(str(exc))
        finally:self._loading=False;self.settings_changed()

    def get_selection(self):
        run=self.current_run()
        selection=Selection(utc_us(self.start.text()),utc_us(self.end.text()),parse_channels(self.channels.text(),run.first.channels))
        selection.resolve(run)
        return selection

    def get_recipe(self):
        bands=tuple(tuple(float(x) for x in b.split(":")) for b in self.band.text().split(",") if b.strip())
        return AnalysisRecipe(quantity=self.quantity.currentText(),spectral_window_s=self.window.text(),
            welch_hop_s=self.welch_hop.text(),stft_hop_s=self.stft_hop.text(),nfft=int(self.nfft.text()) if self.nfft.text() else None,
            detrend=self.detrend.currentText(),rms_bin_s=self.rms_bin.text(),ac_rms=self.ac.isChecked(),
            bands=bands,spectrogram=self.save_spec.isChecked(),backend=self.backend.currentData(),strict_quality=self.strict.isChecked())

    def settings_changed(self,*args):
        if self._loading:return
        if self.result:self.snapshot.setText("Controls edited · displayed result retains its original saved settings. Run again to calculate the new selection.")
        try:
            run=self.current_run();selection=self.get_selection();recipe=self.get_recipe();r=recipe.resolve(run.first)
            s=selection.resolve(run)
            self.resolution.setText(f"Window {r['window']:,} samples · Welch hop {r['welch_hop']:,} · STFT hop {r['stft_hop']:,}\nΔf {r['df']:g} Hz · ENBW {r['enbw_hz']:g} Hz · Nyquist {r['fs']/2:g} Hz\n{s['count']:,} samples/channel; first {iso(s['first_us'])}; last {iso(s['last_us'])}")
            e=estimate(run,selection,recipe)
            self.estimate_label.setText(f"Output estimate (uncompressed): {e['final_products_uncompressed_bytes']/1e6:,.1f} MB\nCheckpoints {e['retained_checkpoints_uncompressed_bytes']/1e6:,.1f} MB; temporary consolidation {e['temporary_consolidation_bytes']/1e6:,.1f} MB.\nValid coverage determined during the job.")
            self.run_button.setEnabled(True)
        except Exception as exc:
            self.resolution.setText(str(exc));self.run_button.setEnabled(False)

    def _save_controls(self):
        if not self.project_path:return
        try:
            with Project(self.project_path) as p:
                p.document["last_controls"]=dict(start=self.start.text(),end=self.end.text(),channels=self.channels.text(),recipe=jsonable(self.get_recipe()),run_id=self.current_run().id)
                p.save()
        except Exception:pass

    def set_controls(self,state):
        if state.get("run_id"):
            for i,run in enumerate(self.runs):
                if run.id==state["run_id"]:self.tree.setCurrentItem(self.tree.topLevelItem(i));break
        self._loading=True
        r=recipe_from_dict(state["recipe"])
        for w,v in [(self.start,state.get("start","")),(self.end,state.get("end","")),(self.channels,state.get("channels","0")),
                    (self.window,r.spectral_window_s),(self.welch_hop,r.welch_hop_s),(self.stft_hop,r.stft_hop_s),
                    (self.nfft,"" if r.nfft is None else str(r.nfft)),(self.rms_bin,r.rms_bin_s),(self.band,",".join(f"{a}:{b}" for a,b in r.bands))]:w.setText(str(v))
        self.quantity.setCurrentText(r.quantity);self.detrend.setCurrentText(r.detrend)
        index=self.backend.findData(r.backend)
        if index<0:
            self.backend.addItem(r.backend+" · unavailable here; CPU fallback",r.backend);index=self.backend.count()-1
        self.backend.setCurrentIndex(index)
        self.ac.setChecked(r.ac_rms);self.strict.setChecked(r.strict_quality);self.save_spec.setChecked(r.spectrogram)
        self._loading=False;self.settings_changed()

    def preview_selection(self):
        try:
            selection=self.get_selection()
            self.start_worker(dict(task="preview",run=jsonable(self.current_run()),roots=self.roots,
                                   selection=jsonable(selection),quantity=self.quantity.currentText(),cache=str(Path(self.project_path)/"display_cache")))
        except Exception as exc:self.error(exc)

    def analyse(self):
        try:
            run,selection,recipe=self.current_run(),self.get_selection(),self.get_recipe()
            directory=Path(self.project_path)/"jobs"/(datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:6])
            with Project(self.project_path) as p:doi=p.document.get("source_doi")
            create_job(directory,run,self.roots,selection,recipe,source_doi=doi)
            self._save_controls();self.pending.append(str(directory));self.refresh_jobs();self.next_job()
        except Exception as exc:self.error(exc)

    def full_fibre_overview(self):
        if self.process:return self.error("Finish the active task before generating an overview.",trusted=True)
        try:
            dialog=OverviewDialog(self.current_run(),utc_us(self.start.text()),utc_us(self.end.text()),self)
            dialog.strict.setChecked(self.strict.isChecked())
            if dialog.exec()==Q.QDialog.Accepted:self.generate_overview(dialog.settings(),dialog.planning_decision)
        except Exception as exc:self.error(exc)

    def generate_overview(self,settings,planning_decision=None):
        try:
            run=self.current_run();start,end=utc_us(self.start.text()),utc_us(self.end.text())
            resolve_overview(run,start,end,settings)
            directory=Path(self.project_path)/"overviews"/(datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:6])
            with Project(self.project_path) as p:doi=p.document.get("source_doi")
            self.start_worker(dict(task="overview",run=jsonable(run),roots=self.roots,start_us=start,end_us=end,
                                   settings=settings,source_doi=doi,directory=str(directory),planning_decision=planning_decision))
        except Exception as exc:self.error(exc)

    def show_fibre_overview(self,directory):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg,NavigationToolbar2QT
        from matplotlib.text import Text
        class OverviewToolbar(NavigationToolbar2QT):
            toolitems=tuple(t for t in NavigationToolbar2QT.toolitems if t[0] not in ("Save","Customize","Subplots"))
        class OverviewCanvas(FigureCanvasQTAgg):
            """Scale preview typography with the viewport; export keeps physical sizes."""
            def __init__(self,figure):
                super().__init__(figure)
                self.original_fonts={t:t.get_fontsize() for t in figure.findobj(Text)}
                self.reference_pixels=figure.get_size_inches()*figure.dpi
            def resizeEvent(self,event):
                factor=min(event.size().width()/self.reference_pixels[0],event.size().height()/self.reference_pixels[1])
                for text,size in self.original_fonts.items():text.set_fontsize(size*factor)
                super().resizeEvent(event)
        figure=make_overview_figure(directory,dict(dpi=110))
        if self.overview_toolbar:
            self.fibre_layout.removeWidget(self.overview_toolbar);self.overview_toolbar.hide();self.overview_toolbar.deleteLater()
        if self.overview_canvas:
            self.fibre_layout.removeWidget(self.overview_canvas);self.overview_canvas.hide();self.overview_canvas.figure.clear();self.overview_canvas.deleteLater()
        self.overview_canvas=OverviewCanvas(figure)
        self.overview_canvas.setMinimumSize(440,330)
        self.overview_toolbar=OverviewToolbar(self.overview_canvas,self)
        self.fibre_layout.addWidget(self.overview_toolbar);self.fibre_layout.addWidget(self.overview_canvas,1)
        self.overview_canvas.draw_idle();self.overview_directory=str(directory)
        self.fibre_export.setEnabled(True);self.tabs.setCurrentWidget(self.fibre_tab)
        m=read_json(Path(directory)/"overview_manifest.json")
        from .overview_evidence import evidence_status
        evidence=evidence_status(directory,m)
        self.snapshot.setText(f"Saved full-fibre overview · {m['fs']:g} Hz · trace channel {m['settings']['trace_channel']:,}\n{iso(m['selection']['first_us'])} → {iso(m['selection']['end_exclusive_us'])} (end exclusive)")
        self.fibre_note.setText(f"Saved overview · {m['estimate']['all_channels']:,} channels · {m['selection']['count']:,} native samples/channel · {m['quality']['excluded_samples']:,} excluded.\n"
            f"Derived-result validation: {evidence['status']}. Hover here for scope and binding details.\n"
            "Use pan/zoom for inspection. Export figure and data uses the full saved extent and includes numerical data.")
        self.fibre_note.setToolTip(evidence["explanation"])
        self.plot_info.setText("Waterfall reduction is display-only. Waveform uses native samples; per-channel temporal RMS uses all channels and valid samples. Spectral controls do not affect this saved overview.")

    def export_fibre_overview(self):
        if not self.overview_directory:return self.error("Generate or open a saved fibre overview first.",trusted=True)
        dialog=OverviewExportDialog(self)
        if dialog.exec()!=Q.QDialog.Accepted:return
        path=Q.QFileDialog.getExistingDirectory(self,"Choose a parent folder for the three-panel publication bundle")
        if path:
            destination=Path(path)/("DAS_overview_"+datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:4])
            self.start_worker(dict(task="overview_export",directory=self.overview_directory,destination=str(destination),settings=dialog.settings()))

    def next_job(self):
        if not self.process and self.pending:
            directory=self.pending.pop(0)
            self.start_worker(dict(task="analyse",directory=directory,roots=self.roots))

    def start_worker(self,request):
        if self.process:return self.error("A task is active. Pause or finish it before starting this operation; analyses can be added to the queue.",trusted=True)
        context=mp.get_context("spawn")
        self.messages=context.Queue(maxsize=128);self.cancel_event=context.Event();self.pause_event=context.Event()
        self.current_task=request
        self._overview_stopping=False
        if hasattr(self,"_dead_since"):del self._dead_since
        if request['task']=='overview':
            from .overview_jobs import register
            register(request['directory'])
        self.pause_button.setText("Stop overview" if request['task']=='overview' else "Pause")
        self.cancel_button.setVisible(request['task']!='overview')
        self.process=context.Process(target=worker_main,args=(request,self.messages,self.cancel_event,self.pause_event),daemon=False)
        self.process.start();self.status.setText(f"{request['task'].capitalize()} started");self.progress.setRange(0,0)
        self.preview_button.setEnabled(False)

    def poll(self):
        if not self.process:return
        terminal=None
        for _ in range(150):
            try:msg=self.messages.get_nowait()
            except queue.Empty:break
            if msg["type"]=="progress":
                self.progress.setRange(0,max(1,msg["total"]));self.progress.setValue(msg["done"])
                if not self._overview_stopping:self.status.setText(f"{msg['done']:,}/{msg['total']:,} · {msg['label']}")
            else:terminal=msg;break
        if terminal:
            request=self.current_task
            self.process.join(timeout=.1)
            if self.process.is_alive():
                # Final result already arrived; orderly worker exit should be imminent.
                self.process.join(timeout=.5)
            self.process=None;self.current_task=None;self.preview_button.setEnabled(True)
            self.pause_button.setText("Pause");self.cancel_button.setVisible(True)
            self.progress.setRange(0,100);self.progress.setValue(100)
            if terminal["type"]=="stopped":
                self.status.setText("Interrupted — regenerate. Uncommitted overview discarded; restart from the beginning.");self.refresh_jobs()
            elif terminal["type"]=="error":
                self.retain_error(terminal["message"]+"\n\n"+terminal["traceback"])
                self.status.setText("The operation could not be completed. Open Error details for diagnostic information.")
                self.refresh_jobs()
            else:self.handle_result(request,terminal["result"])
            QtCore.QTimer.singleShot(100,self.next_job)
        elif not self.process.is_alive():
            # Queue feeder may deliver the terminal message just after process exit.
            if not hasattr(self,"_dead_since"):self._dead_since=time.monotonic()
            elif time.monotonic()-self._dead_since>1:
                overview=self.current_task['task']=='overview'
                self.status.setText("Interrupted — regenerate. Uncommitted overview discarded." if overview else "Worker stopped unexpectedly. Completed units are retained; resume the job.")
                if overview:
                    from .overview_jobs import reconcile, record_path
                    state=reconcile(record_path(self.current_task['directory']),force=True)
                    if state['state']=='completed':self.status.setText("Overview completed before worker exit; committed result retained.")
                self.process=None;self.current_task=None;self.preview_button.setEnabled(True);del self._dead_since
                self.pause_button.setText("Pause");self.cancel_button.setVisible(True);self.refresh_jobs()

    def handle_result(self,request,result):
        task=request["task"]
        self.status.setText(f"{task.capitalize()} complete")
        if task=="scan":
            self.roots=result["roots"];self.runs=[run_from_dict(x) for x in result["runs_data"]];self.populate_runs()
            requested=[Path(p).resolve() for p in request.get("paths",[]) if Path(p).is_file()]
            if requested:
                for ri,run in enumerate(self.runs):
                    for fi,source in enumerate(run.files):
                        if (Path(self.roots[source.root_id])/source.relative_path).resolve() in requested:
                            self.tree.setCurrentItem(self.tree.topLevelItem(ri).child(fi))
            self.status.setText(f"{result.get('files',result.get('seen',0))} recordings · {len(self.runs)} runs · {len(result['errors'])} import errors")
            if result["errors"] or result["conflicts"]:
                self.details.setPlainText(json.dumps(result["errors"]+result["conflicts"],indent=2));self.tabs.setCurrentWidget(self.details)
        elif task=="preview":self.show_preview(result)
        elif task=="overview":
            self.refresh_jobs()
            try:self.show_fibre_overview(result)
            except Exception as exc:self.error(exc)
        elif task=="overview_export":self.status.setText(f"Three-panel publication bundle saved: {result}")
        elif task=="analyse":
            self.refresh_jobs()
            self.status.setText(f"Job {result['state']} · {result.get('completed_units',0)} units retained")
            if result["state"]=="completed":
                self.result_directory=request["directory"]
                QtCore.QTimer.singleShot(50,lambda:self.load_result(self.result_directory))
        elif task=="result":self.show_result(request["directory"],result)
        elif task=="export":self.status.setText(f"Figure and data saved: {result}")
        elif task=="diagnostics":self.details.setPlainText(json.dumps(result,indent=2));self.tabs.setCurrentWidget(self.details)

    def show_preview(self,preview):
        self.preview=preview;self.wave.clear();self.overview.clear()
        t=preview["time_s"]
        self.wave.plot(t,preview["minimum"][0],pen=pg.mkPen("#1b738a",width=1))
        self.wave.plot(t,preview["maximum"][0],pen=pg.mkPen("#1b738a",width=1))
        self.wave.setLabel("left",f"Channel {preview['channels'][0]}",preview["unit"])
        self.selection_region=pg.LinearRegionItem(values=(0,max(t[-1],.001)),brush=(20,110,140,25))
        self.selection_region.sigRegionChangeFinished.connect(self.graph_selection)
        self.wave.addItem(self.selection_region)
        im=pg.ImageItem(preview["overview"])
        im.setLookupTable(pg.colormap.get("viridis").getLookupTable())
        im.setRect(QtCore.QRectF(0,-.5,max(t[-1],.001),len(preview["channels"])))
        self.overview.addItem(im);self.overview.autoRange()
        self.plot_info.setText(preview["display_reduction"]+". Overview rows: "+", ".join(map(str,preview["channels"])))
        self.tabs.setCurrentWidget(self.wave)

    def graph_selection(self):
        if not self.preview:return
        lo,hi=sorted(self.selection_region.getRegion())
        base=self.preview["selection"]["first_us"]
        end=self.preview["selection"]["end_exclusive_us"]
        self.start.setText(iso(max(base,base+round(lo*1e6))))
        self.end.setText(iso(min(end,base+round(hi*1e6))))

    def load_result(self,directory,channel_index=0):
        if not directory:return
        self.start_worker(dict(task="result",directory=str(directory),channel_index=channel_index))

    def show_result(self,directory,view):
        self.result_directory=directory;self.result=view
        manifest=view["manifest"];r=manifest["recipe"];amp=r["amplitude_unit"]
        self._loading=True
        if self.result_channel.count()!=len(manifest["selection"]["channels"]) or self.result_channel.itemText(0)!=str(manifest["selection"]["channels"][0]):
            self.result_channel.clear();self.result_channel.addItems([str(c) for c in manifest["selection"]["channels"]])
        self.result_channel.setCurrentText(str(view["channel"]));self._loading=False
        self.psd.clear();self.psd.plot(view["frequency"],display_db(view["psd"]),pen=pg.mkPen("#176c80",width=1.2));self.psd.setLabel("left",f"PSD · dB re 1 {amp}²/Hz")
        self.spec.clear()
        if view["stft"] is not None:
            im=pg.ImageItem(display_db(view["stft"]))
            im.setLookupTable(pg.colormap.get("viridis").getLookupTable())
            times,freq=view["stft_time"],view["stft_frequency"]
            dt=r["actual_stft_hop_s"]*view["time_stride"];df=r["df"]*view["frequency_stride"]
            im.setRect(QtCore.QRectF(times[0]-dt/2,freq[0]-df/2,max(dt,len(times)*dt),max(df,len(freq)*df)))
            self.spec.addItem(im);self.spec.autoRange();self.spec.setYRange(0,r["fs"]/2,padding=0)
            # Explicit units and colour scale endpoints stay visible without hiding
            # invalid columns, which remain transparent/NaN.
            finite=display_db(view["stft"]);finite=finite[np.isfinite(finite)]
            if len(finite):
                lo,hi=float(finite.min()),float(finite.max())
                if hi<=lo:hi=lo+1
                if self.spec_bar is None:
                    self.spec_bar=pg.ColorBarItem(values=(lo,hi),colorMap=pg.colormap.get("viridis"),label=f"dB re 1 {amp}²/Hz")
                    self.spec_bar.setImageItem(im,insert_in=self.spec.plotItem)
                else:
                    self.spec_bar.setImageItem(im);self.spec_bar.setLevels((lo,hi))
                    self.spec_bar.axis.setLabel(f"dB re 1 {amp}²/Hz")
                self.spec.setTitle(f"Spectrogram PSD · dB re 1 {amp}²/Hz")
        self.levels.clear();self.levels.plot(view["rms_time"],view["rms"],pen=pg.mkPen("#176c80",width=1.2));self.levels.setLabel("left",f"{'AC-' if r['ac_rms'] else ''}RMS",amp)
        self.snapshot.setText(f"Saved result · channel {view['channel']} · {r['quantity']} · {r['fs']:g} Hz\n{iso(manifest['selection']['first_us'])} → {iso(manifest['selection']['end_exclusive_us'])} · {r['window']}-sample Hann / {r['stft_hop']}-sample STFT hop")
        self.details.setPlainText(json.dumps(manifest,indent=2))
        stats=read_json(Path(directory)/"statistics.json")
        selected_stats=next((x for x in stats if x["channel"]==view["channel"]),{})
        self.statistics.setPlainText("Selected channel; complete channel tables are saved in statistics.json.\n\n"+json.dumps(selected_stats,indent=2))
        self.plot_info.setText(f"STFT: every {view['time_stride']} column / {view['frequency_stride']} bin for display; native numeric arrays retained. No time averaging. Zeros displayed at −180 dB only.")
        self.tabs.setCurrentWidget(self.psd)

    def result_channel_changed(self,index):
        if self._loading or index<0 or not self.result_directory:return
        self.load_result(self.result_directory,index)

    def refresh_jobs(self):
        if not self.project_path:return
        self.jobs.clear()
        for p in sorted((Path(self.project_path)/"overviews").glob("*/overview_manifest.json"),reverse=True):
            try:
                m=read_json(p)
                item=Q.QListWidgetItem(f"Overview · {m['estimate']['all_channels']:,} ch · {p.parent.name}")
                item.setData(QtCore.Qt.UserRole,str(p.parent));self.jobs.addItem(item)
            except Exception:pass
        from .overview_jobs import reconcile
        for p in sorted((Path(self.project_path)/"overviews/.overview_jobs").glob("*.json"),reverse=True):
            try:
                state=reconcile(p)
                if state['state']!='completed':
                    item=Q.QListWidgetItem(f"Overview · Interrupted — regenerate · {state['destination']}")
                    item.setData(QtCore.Qt.UserRole,str(p));self.jobs.addItem(item)
            except Exception:pass
        for p in sorted((Path(self.project_path)/"jobs").glob("*/job.json"),reverse=True):
            try:
                job=read_json(p)
                from .overview_jobs import is_legacy_overview
                if is_legacy_overview(job):
                    label=f"Overview · Interrupted — regenerate · {p.parent.name}"
                else:
                    s=job["identity"]["selection"]
                    label=f"{job['state']} · {len(s['channels'])} ch · {p.parent.name}"
                item=Q.QListWidgetItem(label)
                item.setData(QtCore.Qt.UserRole,str(p.parent));self.jobs.addItem(item)
            except Exception:pass

    def open_saved_job(self,item):
        path=item.data(QtCore.Qt.UserRole)
        if Path(path).is_file():
            self.details.setPlainText(json.dumps(read_json(path),indent=2));self.tabs.setCurrentWidget(self.details);return
        if (Path(path)/"overview_manifest.json").exists():
            try:self.show_fibre_overview(path)
            except Exception as exc:self.error(exc)
        elif (Path(path)/"result_manifest.json").exists():self.load_result(path)
        else:self.details.setPlainText(json.dumps(read_json(Path(path)/"job.json"),indent=2));self.tabs.setCurrentWidget(self.details)

    def resume_selected(self):
        item=self.jobs.currentItem()
        if item:
            path=item.data(QtCore.Qt.UserRole)
            if (Path(path)/"overview_manifest.json").exists():return self.show_fibre_overview(path)
            from .overview_jobs import is_legacy_overview
            if Path(path).is_file() or is_legacy_overview(read_json(Path(path)/'job.json')):
                self.status.setText("Interrupted — regenerate. Choose the interval and Generate full-fibre overview to restart.");return
            if path not in self.pending:self.pending.append(path)
            self.next_job()

    def browse_result(self):
        path=Q.QFileDialog.getExistingDirectory(self,"Open a result or portable export folder")
        if path:
            if (Path(path)/"overview_manifest.json").exists():
                try:self.show_fibre_overview(path)
                except Exception as exc:self.error(exc)
            else:self.load_result(path)

    def stop_overview(self):
        self.cancel_event.set();self._overview_stopping=True
        # UI acknowledgement is immediate; disk completion is separately bounded.
        self.status.setText("Stopping overview… in-flight read/compute may finish. Uncommitted work will be discarded; regenerate from the beginning.")
        self.status.repaint()

    def pause(self):
        if self.process and self.current_task['task']=='overview':return self.stop_overview()
        if self.process:
            self.pause_event.set();self.status.setText("Pause acknowledged · waiting for a safe unit boundary")

    def cancel(self):
        if self.process and self.current_task['task']=='overview':return self.stop_overview()
        self.pending.clear()
        if self.process:
            self.cancel_event.set();self.status.setText("Cancel acknowledged · completed units are retained")

    def pin_psd(self):
        if not self.result:return
        if len(self.pins)>=8:return self.error("At most eight PSD comparisons are shown at once. Start a new comparison session to add more.",trusted=True)
        r=self.result;unit=r["manifest"]["recipe"]["amplitude_unit"]
        if self.pins and self.pins[0]["unit"]!=unit:return self.error("Comparison requires the same quantity and PSD units.",trusted=True)
        label=f"ch {r['channel']} · {r['manifest']['recipe']['fs']:g} Hz · {iso(r['manifest']['selection']['first_us'])} · {r['manifest']['acquisition_id'][:8]}"
        name,ok=Q.QInputDialog.getText(self,"Name comparison item","Label",text=label)
        if not ok:return
        self.pins.append(dict(unit=unit,label=name,path=self.result_directory,channel=r["channel"]))
        self.comparisons.plot(r["frequency"],display_db(r["psd"]),pen=pg.mkPen(pg.intColor(len(self.pins)-1,hues=8),width=1.1),name=name)
        self.comparisons.setLabel("left",f"PSD · dB re 1 {unit}²/Hz");self.tabs.setCurrentWidget(self.comparisons)
        if self.project_path:atomic_json(Path(self.project_path)/"comparison_items.json",self.pins)

    def export(self):
        if self.tabs.currentWidget()==self.fibre_tab:return self.export_fibre_overview()
        if not self.result_directory:return self.error("Calculate or open a saved result before exporting.",trusted=True)
        dialog=ExportDialog(self)
        if dialog.exec()!=Q.QDialog.Accepted:return
        path=Q.QFileDialog.getExistingDirectory(self,"Choose a parent folder for the publication bundle")
        if not path:return
        try:
            out=Path(path)/("DAS_figure_"+datetime.now().strftime("%Y%m%d_%H%M%S"))
            self.start_worker(dict(task="export",directory=self.result_directory,destination=str(out),
                                   channel_index=max(0,self.result_channel.currentIndex()),settings=dialog.settings()))
        except Exception as exc:self.error(exc)

    def save_recipe(self):
        try:
            recipe=self.get_recipe();recipe.resolve(self.current_run().first)
            path,_=Q.QFileDialog.getSaveFileName(self,"Save duration-based recipe","recipe.json","JSON (*.json)")
            if path:atomic_json(path,jsonable(recipe))
        except Exception as exc:self.error(exc)

    def load_recipe(self):
        path,_=Q.QFileDialog.getOpenFileName(self,"Load a recipe",filter="JSON (*.json)")
        if path:
            try:
                recipe=read_json(path)
                if "requested" in recipe:recipe=recipe["requested"]
                self.set_controls(dict(start=self.start.text(),end=self.end.text(),channels=self.channels.text(),recipe=recipe))
            except Exception as exc:self.error(exc)

    def distance_selection(self):
        try:
            s=self.current_run().first
            if s.spacing_m is None or s.start_locus is None:raise ValueError("This acquisition has no verified relative channel coordinate metadata.")
            text,ok=Q.QInputDialog.getText(self,"Relative fibre distance","Metres: lower:upper (upper excluded)",text="0:100")
            if ok:
                lo,hi=map(float,text.split(":"))
                if not 0<=lo<hi:raise ValueError("Choose increasing nonnegative distances.")
                a=max(0,math.ceil(lo/s.spacing_m-s.start_locus));b=min(s.channels,math.ceil(hi/s.spacing_m-s.start_locus))
                if a>=b:raise ValueError("No channels in this distance interval.")
                self.channels.setText(f"{a}:{b}")
        except Exception as exc:self.error(exc)

    def relink(self):
        if self.process:return self.error("Pause the active task before relinking sources.",trusted=True)
        if not self.roots:return
        choices=[f"{k} | {v}" for k,v in self.roots.items()]
        choice,ok=Q.QInputDialog.getItem(self,"Relink source root","Choose the root",choices,editable=False)
        if not ok:return
        new=Q.QFileDialog.getExistingDirectory(self,"Choose the relocated root")
        if new:
            try:
                with Project(self.project_path) as p:p.relink(choice.split(" | ")[0],new)
                self.load_project(self.project_path)
            except Exception as exc:self.error(exc)

    def demo(self):
        if self.process:return self.error("Finish or pause the active task before creating the demo.",trusted=True)
        if not self.ensure_project():return
        from .fixtures import make_demo
        folder=Path(self.project_path)/"synthetic_sources"
        if not folder.exists():make_demo(folder)
        self.scan([str(folder)])

    def diagnostics(self):
        self.start_worker(dict(task="diagnostics",destination=str(Path(self.project_path)/"diagnostics.json") if self.project_path else None))

    def help(self):
        Q.QMessageBox.information(self,"Quick start","1. Create/open a project on your local disk.\n2. Open a recording or add a folder; indexing reads metadata.\n3. Select a continuous run, UTC interval and zero-based channels.\n4. Preview the waveform, then Run analysis.\n5. Browse channels, pin PSD comparisons and export figure and data.\n\nUse channel range 1:21 for channels 1–20. File/folder joins do not restart windows. STFT columns are not time-averaged.\n\nThe window controls the duration contributing to an estimate; hop controls output spacing. Zero padding changes the grid, not physical resolution.\n\nSpectral jobs can pause and resume from checked units. Overviews Stop and discard uncommitted work; regenerate to restart. Phase is not calibrated pressure or strain. Automatic processing currently uses CPU. Available GPU processing includes a CPU numerical check.")

    def closeEvent(self,event):
        if self.process:
            if self.current_task['task']=='overview':self.stop_overview()
            else:self.pause_event.set();self.status.setText("Pausing safely before closing; close again after the job stops.")
            event.ignore();return
        self._save_controls();event.accept()


def launch(project=None):
    app=Q.QApplication.instance() or Q.QApplication([])
    app.setApplicationName("DAS Workbench");app.setOrganizationName("DAS Research")
    window=MainWindow(project);window.show()
    return app.exec()
