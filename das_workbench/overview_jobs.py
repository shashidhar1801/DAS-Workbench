"""Small overview-only job catalogue; spectral checkpoints are deliberately separate.

Publication is one directory rename. Catalogue completion follows it; recovery
checks the published artifact first, then cleans only a token-owned staging path.
"""
from pathlib import Path
import os
import shutil
import uuid
from .contracts import atomic_json, read_json


def record_path(destination):
    d = Path(destination)
    return d.parent/'.overview_jobs'/(d.name+'.json')


def register(destination):
    d = Path(destination)
    if d.exists(): raise FileExistsError('Choose a new overview result folder.')
    path = record_path(d)
    if path.exists(): raise FileExistsError('This overview attempt already exists; regenerate into a new folder.')
    token = uuid.uuid4().hex
    atomic_json(path, dict(kind='overview', state='queued', destination=d.name,
                          staging='.overview-'+token, token=token, pid=None,
                          explanation='No resumable units. Interrupted — regenerate from the beginning.'))
    return path


def update(path, **fields):
    item=read_json(path);item.update(fields);atomic_json(path,item)


def _alive(pid):
    if not pid:return False
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


def reconcile(path, force=False):
    path=Path(path);item=read_json(path);base=path.parent.parent
    destination=base/item['destination']
    # This also repairs a crash after rename but before catalogue completion.
    if destination.exists():
        try:
            from .overview import load_overview
            load_overview(destination)
            item.update(state='completed', explanation='Complete committed overview; no resumable partial units.')
        except Exception as exc:
            item.update(state='failed', explanation='Published artifact failed integrity check: '+str(exc))
    elif item['state'] not in ('completed',) and (force or not _alive(item.get('pid'))):
        item.update(state='interrupted', explanation='Interrupted — regenerate. Uncommitted work discarded; restart from the beginning.')
    elif item['state']=='completed':
        item.update(state='interrupted', explanation='Result missing — regenerate; no overview checkpoint exists.')
    if item['state'] in ('interrupted','failed','completed'):
        staging=base/item['staging']
        # A record cannot authorize broad glob cleanup or removal of another job.
        if staging.name=='.overview-'+item['token'] and staging.is_dir() and not staging.is_symlink():
            owner=staging/'.overview_owner.json'
            if owner.exists() and read_json(owner).get('token')==item['token']:
                shutil.rmtree(staging)
    atomic_json(path,item)
    return item


def run_overview_job(destination, *args, **kwargs):
    from .overview import compute_overview
    path=record_path(destination)
    if not path.exists():register(destination)
    item=read_json(path)
    if item['state'] not in ('queued',):
        raise ValueError('Interrupted overview must regenerate into a new result folder; it cannot resume.')
    staging=path.parent.parent/item['staging']
    staging.mkdir(parents=True, exist_ok=False)
    atomic_json(staging/'.overview_owner.json',dict(token=item['token']))
    update(path,state='running',pid=os.getpid())
    try:
        result=compute_overview(destination,*args,staging=staging,**kwargs)
        update(path,state='completed',explanation='Complete committed overview.')
        return result
    finally:
        # Completed output wins a Stop/commit race; otherwise no result is published.
        reconcile(path,force=True)


def is_legacy_overview(job):
    identity=job.get('identity',{})
    return (job.get('kind')=='overview' or job.get('task')=='overview' or job.get('type')=='overview' or
            'overview' in str(job.get('algorithm','')).lower() or
            'overview' in str(identity.get('algorithm','')).lower())
