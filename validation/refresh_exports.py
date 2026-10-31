"""Regenerate release presentation only, preserving finalized numeric HDF5."""
from pathlib import Path
import shutil
from das_workbench.contracts import read_json,atomic_json,file_hash
from das_workbench.overview_evidence import attach_report,evidence_status
from das_workbench.overview import export_overview
root=Path('reports/v0.2.1');archive=Path('work_v021/first_presentation');archive.mkdir(parents=True,exist_ok=True)
for case in ['D01','D02','D03','D04']:
    result=root/'real'/(case+'_result');bundle=root/'real'/(case+'_bundle')
    if case=='D02':
        report=read_json(result/'validation_report.json');prod=report['execution']['production']
        prod['numeric_file_created_this_invocation']=False;prod['peak_observed_process_rss_bytes']=None
        prod['coverage']='Metadata reuse only; production RSS not measured for this retained result. Original compute wall time is from saved manifest; independent-reference RSS was measured separately.'
        report=attach_report(result,report);atomic_json(root/'real'/(case+'_validation.json'),report)
    old=archive/(case+'_bundle');shutil.move(bundle,old)
    checksum=file_hash(result/'overview.h5');export_overview(result,bundle)
    assert file_hash(result/'overview.h5')==checksum
    assert evidence_status(bundle)['status']=='passed'
    audit=read_json(root/'real'/(case+'_bundle_audit.json'));audit['presentation_refresh']='Plain-text logarithmic RMS tick labels for SVG viewer compatibility; numerical artifact unchanged.'
    atomic_json(root/'real'/(case+'_bundle_audit.json'),audit)
