"""Audit a packaging-only release without rewriting historical result evidence.

Run from the repository root. Original recordings are not needed for bundle
integrity checks. --previous-source binds the scientific-source comparison.
"""
import argparse
import hashlib
import json
import plistlib
import subprocess
from pathlib import Path
from das_workbench import __version__
from das_workbench.public_engine import load_result, evidence

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
    return h.hexdigest()

def main():
    a=argparse.ArgumentParser()
    a.add_argument('--previous-source',type=Path,required=True)
    a.add_argument('--app',type=Path,required=True)
    a.add_argument('--examples',type=Path,required=True)
    a.add_argument('--output',type=Path,required=True)
    args=a.parse_args();root=Path(__file__).resolve().parents[1]
    changed=[];unchanged=[]
    for old in sorted((args.previous_source/'das_workbench').glob('*.py')):
        rel=old.relative_to(args.previous_source);new=root/rel
        entry={'file':str(rel),'before_sha256':sha(old),'after_sha256':sha(new)}
        (changed if entry['before_sha256']!=entry['after_sha256'] else unchanged).append(entry)
    assert {v['file'] for v in changed}=={'das_workbench/__init__.py','das_workbench/presentation.py'}
    # Check every saved result nested in the preserved example folders.
    bundles=[]
    for p in sorted(args.examples.rglob('manifest.json')):
        m,r=load_result(p.parent);ev=evidence(p.parent)
        assert ev['status']=='passed',(p,ev['status'])
        bundles.append({'folder':str(p.parent.relative_to(args.examples)),
            'original_app_version':m['app_version'],'numeric_sha256':m['numeric_sha256'],
            'recipe_sha256':m['recipe_sha256'],'scope_sha256':m['scope_sha256'],
            'historical_validation_status':ev['status'],
            'checks':len(ev['report']['checks']),
            'fresh_check':'Saved numeric/recipe/scope and validation-sidecar integrity only'})
    leftovers=[str(p.relative_to(args.app)) for p in args.app.rglob('*') if 'virtualkeyboard' in str(p).lower()]
    assert not leftovers,leftovers
    sig=subprocess.run(['codesign','--verify','--deep','--strict',str(args.app)],capture_output=True,text=True)
    assert sig.returncode==0,sig.stderr
    info=plistlib.loads((args.app/'Contents/Info.plist').read_bytes())
    exe=args.app/'Contents/MacOS/DAS Workbench'
    notices=args.app/'Contents/Resources/licenses/qt_notices'
    assert notices.is_dir()
    assert list(notices.rglob('LGPL-3.0-only.txt'))
    report={'version':__version__,'status':'passed','source_changes':changed,
        'unchanged_application_modules':unchanged,
        'saved_example_integrity_checks':bundles,
        'app':{'bundle_version':info['CFBundleVersion'],'short_version':info['CFBundleShortVersionString'],
            'executable_sha256':sha(exe),'codesign_verify':'passed','notarization':'not notarized',
            'unused_virtual_keyboard_entries':leftovers,'qt_notices_present':True},
        'limits':['This audit is not a fresh numerical recalculation of the real recordings.',
                  'Historical example sidecars keep their original scope and dates.',
                  'Codesign verification is not Apple notarization or clean-machine installation.',
                  'Interactive desktop evidence must be reported separately.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(f"Passed: {len(unchanged)} unchanged application modules; {len(bundles)} historical bundles checked")

if __name__=='__main__':main()
