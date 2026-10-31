"""Original v0.2.0 saved bundle, with all production source access disabled."""
from pathlib import Path
from unittest.mock import patch
import argparse
import numpy as np
import h5py
from PySide6.QtWidgets import QApplication
from das_workbench.contracts import atomic_json,read_json,file_hash
from das_workbench.overview import load_overview,export_overview
from das_workbench.ui import MainWindow
from das_workbench.overview_evidence import evidence_status


def main(original,output):
    original=Path(original);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    before={p.name:file_hash(p) for p in original.iterdir() if p.is_file()}
    checks=read_json(original/'bundle_checksums.json')
    assert all(file_hash(original/name)==value for name,value in checks.items())
    app=QApplication.instance() or QApplication([])
    with patch('das_workbench.adapter.RunReader',side_effect=AssertionError('Offline source access forbidden')),patch('das_workbench.adapter.assert_signature',side_effect=AssertionError('Offline source access forbidden')):
        m,data=load_overview(original);assert m['software_version']=='0.2.0'
        window=MainWindow();window.show();window.show_fibre_overview(original);app.processEvents()
        assert 'validation: not supplied' in window.fibre_note.text()
        window.overview_canvas.figure.axes[0].set_xlim(10,11)
        export_overview(original,output/'reexport_v021')
        window.grab().save(str(output/'legacy_opened_offline.png'));window.close();app.processEvents()
    _,new=load_overview(output/'reexport_v021')
    assert all(np.array_equal(data[key],new[key],equal_nan=True) for key in data)
    assert file_hash(original/'overview.h5')==file_hash(output/'reexport_v021/overview.h5')
    assert before=={p.name:file_hash(p) for p in original.iterdir() if p.is_file()}
    for name,value in read_json(output/'reexport_v021/bundle_checksums.json').items():assert file_hash(output/'reexport_v021'/name)==value
    report=dict(status='passed',original_numeric_producer=m['software_version'],presentation_version='0.2.1',
        original_checksums_unchanged=before,original_bundle_checksums_verified=True,all_numeric_arrays_exact=True,numeric_file_digest_exact=True,
        offline_open_and_export=True,offline_method='Production RunReader and source signature entry points disabled with raising guards; original portable bundle has no local root map.',
        no_validation_claim_when_absent=evidence_status(original),original_container='Original unpacked v0.2.0 bundle; ZIP-container comparison recorded separately when available.',
        source_validation_preserved=read_json(original/'overview_manifest.json')['source_validation']==read_json(output/'reexport_v021/overview_manifest.json')['source_validation'])
    archive=Path(str(original)+'.zip')
    if archive.exists():
        import hashlib
        from zipfile import ZipFile
        members={};aux=[]
        with ZipFile(archive) as z:
            assert z.testzip() is None
            for item in z.infolist():
                if item.is_dir():continue
                path=Path(item.filename)
                if path.parts[0]=='__MACOSX' or path.name.startswith('._'):
                    aux.append(item.filename);continue
                if path.parts[0]==original.name:path=Path(*path.parts[1:])
                assert not path.is_absolute() and '..' not in path.parts
                members[str(path)]=hashlib.sha256(z.read(item)).hexdigest()==file_hash(original/path)
            assert all(members.values()) and set(members)==set(before)
        report.update(original_container='Original ZIP CRC passed; every scientific bundle member byte-identical to the original directory exercised offline.',original_zip_sha256=file_hash(archive),original_zip_members_match_checked_original=members,zip_auxiliary_appledouble_members_ignored=aux)
    atomic_json(output/'legacy_compatibility.json',report)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('original');parser.add_argument('output');args=parser.parse_args();main(args.original,args.output)
