"""Portable release evidence: baseline/source diff, shared-core hashes and CLI help."""
from pathlib import Path
import tarfile
import difflib
import subprocess
import os
from das_workbench.contracts import atomic_json,read_json,file_hash,digest
base=Path('.');reports=base/'reports/v0.2.1';baseline=base/'work_v021/baseline_v020'
baseline.mkdir(parents=True,exist_ok=True)
with tarfile.open(base/'work_v021/baseline_v020_source.tar.gz') as archive:archive.extractall(baseline,filter='data')
paths=sorted([*base.glob('das_workbench/*.py'),*base.glob('tests/*.py'),*base.glob('packaging/*'),base/'pyproject.toml',base/'launch.py'])
paths=[p for p in paths if p.is_file()]
changes=[];diff=[];same=[]
for path in paths:
    old=baseline/path
    if old.exists() and old.read_bytes()==path.read_bytes():same.append(str(path));continue
    changes.append(str(path))
    oldtext=old.read_text().splitlines(keepends=True) if old.exists() else []
    diff.extend(difflib.unified_diff(oldtext,path.read_text().splitlines(keepends=True),fromfile='v0.2.0/'+str(path),tofile='v0.2.1/'+str(path)))
(reports/'source_changes.diff').write_text(''.join(diff))
core=['adapter.py','contracts.py','engine.py','science.py','project.py','exporter.py']
checks={name:dict(unchanged=file_hash(base/'das_workbench'/name)==file_hash(baseline/'das_workbench'/name),sha256=file_hash(base/'das_workbench'/name)) for name in core}
assert all(x['unchanged'] for x in checks.values())
def kernel(path):
    s=path.read_text();return s[s.index('            for ca in range(0, nc, channel_block):'):s.index('            reason_values, reason_counts =')]
assert kernel(base/'das_workbench/overview.py')==kernel(baseline/'das_workbench/overview.py')
app_paths=[p for p in paths if 'tests' not in p.parts]
app_hashes={str(p):file_hash(p) for p in app_paths}
validator_hashes={str(p):file_hash(p) for p in sorted((base/'validation').glob('*.py'))}
atomic_json(reports/'source_revision.json',dict(app_version='0.2.1',source_revision='sha256:'+digest(app_hashes),method='SHA-256 of canonical sorted relative app-source-path to SHA-256 mapping; workspace is not a Git checkout',files=app_hashes,validator_files=validator_hashes,changed_paths=changes,unchanged_core=checks,overview_native_reduction_text_identical=True,algorithm_ids=['phase-spectra-1','full-fibre-overview-1']))
app=Path('../DAS Workbench v0.2.1.app/Contents/MacOS/DAS Workbench').resolve()
env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','QT_QPA_PLATFORM')};env['PATH']='/usr/bin:/bin'
cli=reports/'cli';cli.mkdir(exist_ok=True)
commands=[]
for name,args in [('help',['--help']),('overview_help',['overview','--help']),('overview_export_help',['overview-export','--help']),('overview_replay_help',['overview-replay','--help']),('smoke_help',['smoke','--help'])]:
    proc=subprocess.run([str(app),*args],env=env,text=True,capture_output=True,check=True)
    (cli/(name+'.txt')).write_text(proc.stdout+proc.stderr);commands.append(dict(command=['APP',*args],returncode=proc.returncode,evidence='reports/cli/'+name+'.txt'))
common=['overview','--project','examples/OOI_project','--run','0','--start','2021-11-02T00:00:15.468000Z','--end','2021-11-02T00:01:15.468000Z','--output','unused_planner_output']
for name,extra,expected in [('blocked_plan',['--plan'],2),('explicit_accepted_plan',['--plan','--accept-suggested-bin'],0),('actionable_cli_failure',[],2)]:
    proc=subprocess.run([str(app),*common,*extra],env=env,text=True,capture_output=True)
    assert proc.returncode==expected and not Path('unused_planner_output').exists()
    (cli/(name+'.txt')).write_text(proc.stdout+proc.stderr);commands.append(dict(command=['APP',*common,*extra],returncode=proc.returncode,evidence='reports/cli/'+name+'.txt'))
    if name=='explicit_accepted_plan':
        import json
        plan=json.loads(proc.stdout);assert plan['display_planning']['requested']['time_bin_samples']==10 and plan['display_planning']['accepted']['time_bin_samples']==16
atomic_json(cli/'executed_commands.json',dict(status='passed',app_version='0.2.1',app_executable_sha256=file_hash(app),commands=commands,placeholder='APP is the separately delivered native app executable; project is the local indexed OOI project, excluded from portable archive'))
