"""Audit and assemble measured tables from completed app workflows; no plots."""
from pathlib import Path
import json,csv,hashlib,shutil,difflib,statistics,sys,platform
from datetime import datetime,timedelta
import h5py,numpy as np


def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for block in iter(lambda:f.read(4*1024**2),b''):h.update(block)
    return h.hexdigest()
def dump(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2))
def table(p,rows):
    with open(p,'w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def assemble(root,data_root,old_queue):
    b=Path(root);runs=b/'local/release_packaged_runs';wf=json.loads((runs/'workflow_summary.json').read_text());assert len(wf)==16 and all(x['frozen'] and x['validation']=='passed' for x in wf)
    # Preserve all earlier generated bundles locally before changing presentation copies.
    old=b/'local/presentation_before_matched_time_axes'
    if not old.exists():shutil.move(b/'examples',old);(b/'examples').mkdir()
    for case in ['F01','F02','F03','F04','S01']:
        parent=(b/'local/release_supplement_runs' if case=='S01' else runs)/case
        src=parent/'run0/bundle';dest=b/'examples'/case
        if not dest.exists():shutil.copytree(src,dest)
        shutil.copy2(dest/'recipe.json',b/'recipes'/(case+'.json'))
        evidence=b/'reports/gui'/case;evidence.mkdir(parents=True,exist_ok=True)
        for p in (parent/'run0').glob('*.png'):shutil.copy2(p,evidence/p.name)
        for p in parent.glob('run*/workflow.json'):
            shutil.copy2(p,evidence/(p.parent.name+'_workflow.json'))
        for p in parent.glob('run*/result/validation_report.json'):
            shutil.copy2(p,b/'reports'/(case+'_'+p.parent.parent.name+'_validation.json'))
    shutil.copy2(runs/'workflow_summary.json',b/'reports/packaged_workflows.json')
    # Scientific consistency and repeatability are distinct from file-byte identity.
    agreements=[];coverage=[];resources=[];bundle_checks={};repeat_checks={};measurements={}
    for case in ['F01','F02','F03','F04','S01']:
        d=b/'examples'/case;m=json.loads((d/'manifest.json').read_text());v=json.loads((d/'validation_report.json').read_text());r=json.loads((d/'recipe.json').read_text());checks=json.loads((d/'bundle_checksums.json').read_text())
        assert all(sha(d/f)==h for f,h in checks.items());assert sha(d/'numeric.h5')==m['numeric_sha256'];assert sha(d/'recipe.json')==m['recipe_sha256'];assert sha(d/m['validation']['file'])==m['validation']['sha256'];assert all(v['checks'].values())
        assert v['validator']['source_sha256']==sha(b/'source/validation/public_reference.py')
        bundle_checks[case]=dict(all_file_checksums_match=True,numeric_sha256=m['numeric_sha256'],recipe_sha256=m['recipe_sha256'],validation_report_sha256=m['validation']['sha256'],validator_source_matches=True)
        metrics=[]
        with h5py.File(d/'numeric.h5') as h:
            for i,(s,z) in enumerate(zip(m['signals'],v['metrics'])):
                g=h[f'signal{i}'];sel=s['selection'];meta=s['metadata'];p=s['accepted'];origin=datetime.fromisoformat(meta['origin'].replace('Z','+00:00'))
                start=(origin+timedelta(seconds=sel['start']*meta['dt'])).isoformat();end=(origin+timedelta(seconds=sel['end']*meta['dt'])).isoformat();ch=g['channels'][:]
                if case!='S01':coverage.append(dict(case=case,record=m['source']['record'],group=meta['group'],native_shape=str(meta['native_shape']),dtype=meta['native_dtype'],axes=' x '.join(meta['native_axes']),Fs_Hz=meta['fs'],dt_s=meta['dt'],native_duration_s=meta['duration'],selected_start_index=sel['start'],selected_end_index_exclusive=sel['end'],selected_N=p['n'],selected_channels=len(ch),trace_index=sel['trace'],quantity=meta['quantity'],unit=meta['unit'],time_basis=meta['clock'],start_display=start,end_display_exclusive=end,coordinate=sel['coordinate'],coordinate_first=float(g['coordinate'][0]),coordinate_last=float(g['coordinate'][-1]),logical_native_bytes=p['native_logical_bytes'],limitation=meta['metadata'].get('findings',meta['metadata'].get('coordinate_finding',meta['calibration']))))
                agreements.append(dict(case=case,group=meta['group'],status=v['status'],structural_checks_pass=all(v['checks'].values()),time_error_over_tolerance=z['time_max_error_over_tolerance'],mean_max_abs_error=z['time_absolute_errors']['mean'],rms_max_abs_error=z['time_absolute_errors']['rms'],trace_max_abs_error=z['time_absolute_errors']['trace'],waterfall_max_abs_error=z['time_absolute_errors']['waterfall'],stft_integrated_relative=z['stft_integrated_relative'],stft_peak_relative=z['stft_peak_relative'],parseval_relative=z['parseval_relative'],welch_integrated_relative=z['welch_integrated_relative'],welch_peak_relative=z['welch_peak_relative'],spectral_gate=1e-10,pressure_relative=z['pressure']['power_relative'] if z['pressure'] else '',pressure_error_db=z['pressure']['level_error_db'] if z['pressure'] else '',pressure_db_gate=1e-8 if z['pressure'] else ''))
                ref=[dict(zip(row.keys(),row.values())) for row in csv.DictReader(open(d/f'signal{i}_statistics.csv'))]
                for key,ds in [('mean','mean'),('population_RMS','rms'),('coordinate','coordinate')]:assert np.array_equal(np.array([float(row[key]) for row in ref]),g[ds][:],equal_nan=True)
                trace_k=int(np.flatnonzero(ch==sel['trace'])[0]);freq=g['spectral/frequency'][:];psd=g['spectral/psd'][:];nonzero=np.flatnonzero(freq>0);peak=int(nonzero[np.argmax(psd[nonzero])]);metric=dict(group=meta['group'],trace_population_rms=float(g['rms'][trace_k]),channel_rms_min=float(np.nanmin(g['rms'][:])),channel_rms_max=float(np.nanmax(g['rms'][:])),welch_positive_frequency_peak_Hz=float(freq[peak]),welch_integrated_power=float(np.sum(psd)*p['df']),nperseg=p['nperseg'],hop=p['hop'],nfft=p['nfft'],window_s=p['window_actual_s'],hop_s=p['hop_actual_s'],df_Hz=p['df'],enbw_Hz=p['enbw_hz'],valid_frames=s['quality_summary']['spectral_valid_frames'],QC=s['quality_summary'],start_display=start,end_display_exclusive=end)
                if 'pressure' in g:
                    levels=g['pressure/level_db'][:];power=g['pressure/mean_square'][:];metric.update(level_min_db=float(levels.min()),level_max_db=float(levels.max()),selection_level_db=float(10*np.log10(np.mean(power)/1e-12)),complete_pressure_windows=len(levels),pressure_coverage_samples=int(g['pressure/valid'][:].sum()*p['pressure_window_samples']))
                metrics.append(metric)
        measurements[case]=metrics
        if case!='S01':
            values=[x for x in wf if x['case']==case];repeat_checks[case]=[]
            for run in values:
                rd=runs/case/f"run{run['run']}"/'result';other=json.loads((rd/'manifest.json').read_text());same=True
                with h5py.File(d/'numeric.h5') as a,h5py.File(rd/'numeric.h5') as c:
                    def visit(k,obj):
                        nonlocal same
                        if isinstance(obj,h5py.Dataset):same &= np.array_equal(obj[:],c[k][:],equal_nan=True)
                    a.visititems(visit)
                assert same
                repeat_checks[case].append(dict(run=run['run'],all_numerical_arrays_exact=True,numeric_file_sha256=other['numeric_sha256']))
                for phase,seconds in run['timings'].items():
                    name=phase.removesuffix('_s');rss=run['resources'].get('metadata' if name=='metadata_open' else name,{})
                    resources.append(dict(case=case,run=run['run'],phase=name,seconds=seconds,gui_peak_RSS_bytes=rss.get('gui_peak_rss',''),workers_peak_RSS_bytes=rss.get('workers_peak_rss',''),RSS_samples=rss.get('samples',''),cache='Uncontrolled OS cache; no eviction; no cold-cache claim',backend='CPU float64',host='Apple M5 Ultra;96GiB;macOS27.0.1 arm64',storage='Inputs external Crucial_2; outputs local filesystem',conditions='Final fixed-case observations run sequentially; no concurrent assistant-started numerical QA/exports'))
    with h5py.File(b/'examples/S01/numeric.h5') as h:
        for name in ['raw_trace','trace','trace_time','channels','mean','rms','waterfall']:
            assert np.array_equal(h['signal0/'+name][:],h['signal1/'+name][:])
    summary=[]
    for case in ['F01','F02','F03','F04']:
        for phase in ['metadata_open','preview','analyse','validate','export']:
            rows=[x for x in resources if x['case']==case and x['phase']==phase];times=[x['seconds'] for x in rows]
            summary.append(dict(case=case,phase=phase,initial_s=times[0],repeat1_s=times[1],repeat2_s=times[2],repeat3_s=times[3],median_s=statistics.median(times),min_s=min(times),max_s=max(times)))
    table(b/'Table1_dataset_coverage.csv',coverage);table(b/'Table2_numerical_agreement.csv',agreements);table(b/'Table3_resource_individual_runs.csv',resources);table(b/'Table3_resource_summary.csv',summary)
    dump(b/'reports/bundle_audit.json',bundle_checks);dump(b/'reports/replay_repeatability.json',repeat_checks);dump(b/'reports/measured_results.json',measurements)
    # Original snapshots are audit input only, not copied into portable output.
    snapshot=json.loads((b/'local/baseline_snapshot.json').read_text());root=Path(data_root);preservation=[]
    for e in snapshot['sources']:
        st=(root/e['relative_path']).stat();assert st.st_size==e['bytes'] and st.st_mtime_ns==e['mtime_ns'];preservation.append(dict(relative_path=e['relative_path'],original_signature_unchanged=True,saved_acquisition_sha256=e['sha256']))
    docs={k:sha(root/'_acquisition'/k)==v for k,v in snapshot['acquisition_documents'].items()}
    assert all(docs.values()),[k for k,v in docs.items() if not v]
    old=b.parent/'DAS_Workbench_v0.2.1_Source_and_Evidence';unchanged={}
    for name,h in snapshot['baseline_source']['files'].items():assert sha(old/name)==h
    for name in ['adapter.py','contracts.py','engine.py','science.py','project.py','exporter.py','overview.py','overview_evidence.py','overview_jobs.py','worker.py']:
        unchanged[name]=sha(old/'das_workbench'/name)==sha(b/'source/das_workbench'/name)
    assert all(unchanged.values())
    oldstop=Path(old_queue)/'STOP';assert oldstop.exists()
    dump(b/'reports/preservation.json',dict(recordings_and_support=preservation,acquisition_documents_unchanged=docs,baseline_source_manifest_unchanged=True,legacy_core_unchanged=unchanged,old_download_STOP_present=True,additional_waveform_downloads=0))
    files={str(p.relative_to(b/'source')):sha(p) for p in sorted((b/'source').rglob('*')) if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts}
    revision=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest();dump(b/'reports/source_revision.json',dict(version='0.3.0',revision='sha256:'+revision,method='Deterministic relative source path to SHA256 manifest; no Git commit invented',files=files,legacy_core_unchanged=unchanged))
    diffs=[]
    for name in files:
        a=old/name;c=b/'source'/name
        try:before=a.read_text().splitlines(True) if a.exists() else [];after=c.read_text().splitlines(True)
        except UnicodeError:continue
        diffs.extend(difflib.unified_diff(before,after,fromfile='v0.2.1/'+name,tofile='v0.3.0/'+name))
    (b/'reports/source_changes.diff').write_text(''.join(diffs))
    print('Audited: bundles, CSV, every repeated numerical array, source/legacy preservation, measured tables.')


if __name__=='__main__':assemble(*sys.argv[1:])
