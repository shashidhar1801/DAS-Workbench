"""Bounded D01–D04 release validation; run each case in a fresh process."""
from pathlib import Path
import argparse
import threading
import time
import platform
import numpy as np
import h5py
import psutil
from das_workbench import __version__
from das_workbench.project import Project
from das_workbench.adapter import validate_time_and_status
from das_workbench.overview import export_overview
from das_workbench.overview_jobs import run_overview_job
from das_workbench.overview_evidence import attach_report,evidence_status
from das_workbench.contracts import read_json,atomic_json,file_hash,iso
from validation.reference_overview import validate


class MemoryObservation:
    def __init__(self):self.stop=threading.Event();self.peak=0;self.count=0
    def __enter__(self):
        self.start=time.perf_counter()
        def sample():
            while not self.stop.is_set():
                self.peak=max(self.peak,psutil.Process().memory_info().rss);self.count+=1;self.stop.wait(.02)
        self.thread=threading.Thread(target=sample);self.thread.start();return self
    def __exit__(self,*exc):self.stop.set();self.thread.join();self.elapsed=time.perf_counter()-self.start
    def result(self):return dict(wall_seconds=self.elapsed,peak_observed_process_rss_bytes=self.peak,sample_count=self.count,sample_period_s=.02,coverage='this process during named phase; not whole-system or OS file cache; sampled peak may miss shorter spikes')


def run_case(case,project,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with Project(project) as p:
        runs=p.runs()[0];roots=dict(p.roots);doi=p.document.get('source_doi')
    c=next(r for r in runs if float(r.first.rate)==1000);ab=next(r for r in runs if float(r.first.rate)==200)
    settings=dict(trace_channel=14692,channel_stride=16,time_bin_samples=10,title='OOI North DAS | full-fibre overview')
    baseline=None
    if case=='D01':
        baseline=Path(project)/'overviews/Nov4_first_minute';old=read_json(baseline/'overview_manifest.json')
        run=ab;start=old['identity']['start_us'];end=old['identity']['end_us'];settings=old['settings']
    elif case=='D02':
        run=c
        # Sorted catalogue; stop on any validation failure rather than silently skip.
        source=c.files[0];validate_time_and_status(source,roots)
        assert source.samples==30000 and source.end_us-source.first_us==30_000_000
        start=source.first_us;end=source.end_us
    elif case=='D03':
        run=ab;a,b=next((a,b) for a,b in zip(ab.files,ab.files[1:]) if a.root_id!=b.root_id)
        assert a.end_us==b.first_us;validate_time_and_status(a,roots);validate_time_and_status(b,roots)
        start=b.first_us-1203*a.tick_us;end=b.first_us+1402*b.tick_us
    elif case=='D04':
        run=c;a,b=c.files[:2];assert a.end_us==b.first_us
        validate_time_and_status(a,roots);validate_time_and_status(b,roots)
        start=b.first_us-6003*a.tick_us;end=b.first_us+7004*b.tick_us
    else:raise ValueError(case)
    directory=output/(case+'_result');bundle=output/(case+'_bundle')
    print(case,'compute',iso(start),iso(end),flush=True)
    progress_at=[0]
    def progress(done,total,label):
        if time.monotonic()-progress_at[0]>10 or done==total:
            print(case,done,total,label,flush=True);progress_at[0]=time.monotonic()
    reused=directory.exists()
    with MemoryObservation() as production:
        if directory.exists():
            m=read_json(directory/'overview_manifest.json')
            assert m['identity']['start_us']==start and m['identity']['end_us']==end
            print(case,'reuse finalized numeric artifact; production timing from manifest',flush=True)
        else:run_overview_job(directory,run,roots,start,end,settings,doi,progress=progress)
    print(case,'independent reference',flush=True)
    with MemoryObservation() as reference:
        report=validate(directory,roots)
    prod=production.result()
    prod['original_compute_wall_seconds']=read_json(directory/'overview_manifest.json')['elapsed_seconds']
    prod['numeric_file_created_this_invocation']=not reused
    if reused:
        prod['coverage']='Metadata reuse only; production RSS not measured for this retained result. Original compute wall time is from its saved manifest.'
        prod['peak_observed_process_rss_bytes']=None
    report['execution']=dict(app_version=__version__,production=prod,reference=reference.result(),
        hardware=dict(machine=platform.machine(),cpu=platform.processor(),logical_cpus=psutil.cpu_count(),physical_ram_bytes=psutil.virtual_memory().total),
        storage='Source: mounted Crucial_2 external volume; result: local workspace volume. Interface/physical bandwidth not measured.',
        performance_context='Single uncontrolled observation. OS/HDF5 cache state not reset or controlled; not a benchmark. CPU float64 overview; GPU not used.')
    if baseline:
        original=read_json(baseline/'overview_manifest.json');equal={}
        with h5py.File(baseline/'overview.h5') as a,h5py.File(directory/'overview.h5') as b:
            def visit(name,obj):
                if isinstance(obj,h5py.Dataset):equal[name]=bool(np.array_equal(obj[:],b[name][:],equal_nan=True))
            a.visititems(visit)
        report['baseline_v020']=dict(numeric_producer=original['software_version'],algorithm=original['algorithm'],numeric_sha256=file_hash(baseline/'overview.h5'),all_dataset_arrays_exact=all(equal.values()),datasets=equal,independent_reference='Rerun under v0.2.1 strict per-channel gate; original v0.2.0 evidence retained separately')
        report['checks']['baseline_arrays']=dict(passed=all(equal.values()),method='Exact equality of every saved dataset with original v0.2.0 numeric artifact')
    report['status']='passed' if all(c['passed'] for c in report['checks'].values()) else 'failed'
    report=attach_report(directory,report)
    atomic_json(output/(case+'_validation.json'),report)
    assert evidence_status(directory)['status']==report['status']
    print(case,'export',report['status'],flush=True)
    with MemoryObservation() as export:
        export_overview(directory,bundle)
    checks=read_json(bundle/'bundle_checksums.json')
    assert 'bundle_checksums.json' not in checks
    assert all(file_hash(bundle/name)==value for name,value in checks.items())
    csv=np.genfromtxt(bundle/'spatial_rms.csv',delimiter=',',names=True)
    with h5py.File(bundle/'overview.h5') as f:
        mappings={'channel_zero_based':'channels','distance_m':'distance_m','ac_rms_rad':'rms_rad','mean_phase_rad':'channel_mean_rad','valid_sample_count':'valid_sample_count'}
        assert all(np.array_equal(csv[col],f[key][:]) for col,key in mappings.items())
    atomic_json(output/(case+'_bundle_audit.json'),dict(status='passed',app_version=__version__,export=export.result(),checksums=True,csv_hdf5_exact=True,validation_binding=evidence_status(bundle),files=list(checks),default_style=read_json(bundle/'figure_settings.json')))
    print(case,'DONE',report['status'],flush=True)
    if report['status']!='passed':raise AssertionError(case+' numerical gate failed')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--case',choices=['D01','D02','D03','D04'],required=True);p.add_argument('--project',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();run_case(a.case,a.project,a.output)
