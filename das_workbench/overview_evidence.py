"""Optional derived-result evidence. Ingestion and payload coverage stay separate."""
from pathlib import Path
from .contracts import digest, read_json, atomic_json, file_hash


def scope_identity(m):
    # No local root map: relocation does not change scientific identity.
    return dict(selection=m['selection'], settings=m['settings'], fs=m['fs'],
                phase_scale=m['phase_scale'], spacing_m=m['spacing_m'], start_locus=m['start_locus'],
                quality=m['quality'], algorithm=m['algorithm'], identity=m['identity'],
                quantity='vendor-processed optical phase', unit='rad')


def evidence_status(directory, manifest=None):
    directory=Path(directory);m=manifest or read_json(directory/'overview_manifest.json')
    result=dict(status='not supplied',binding_valid=False,
                explanation='Independent derived-result validation was not supplied. Ingestion time/status checks and decoded-block coverage do not establish independent agreement.')
    ref=m.get('validation_report')
    if not ref:return result
    try:
        relative=Path(ref['path'])
        if relative.is_absolute() or '..' in relative.parts or len(relative.parts)!=1:
            raise ValueError('validation sidecar must be a relative filename')
        path=directory/relative
        if file_hash(path)!=ref['sha256']:raise ValueError('sidecar digest mismatch')
        report=read_json(path)
        if report['numeric_sha256']!=file_hash(directory/'overview.h5') or report['numeric_sha256']!=m['numeric_sha256']:
            raise ValueError('numeric artifact digest mismatch')
        if read_json(directory/'overview_recipe.json')!=m['identity']:
            raise ValueError('saved recipe differs from manifest identity')
        if report['recipe_sha256']!=digest(m['identity']):raise ValueError('recipe identity mismatch')
        if report['scope_sha256']!=digest(scope_identity(m)):raise ValueError('selection/scope identity mismatch')
        if report['scope']!=scope_identity(m):raise ValueError('scope contents mismatch')
        status=report['status']
        if status not in ('passed','failed','not run','blocked'):raise ValueError('unsupported validation status')
        if not report.get('explanation'):raise ValueError('validation explanation missing')
        for key in ('validator','method','environment','thresholds','checks'):
            if key not in report:raise ValueError('incomplete validation report: '+key)
        if status=='passed' and (not report['checks'] or not all(c.get('passed') is True for c in report['checks'].values())):
            raise ValueError('passed report contains missing or unsuccessful checks')
        return dict(status=status,binding_valid=True,explanation=report['explanation'])
    except (OSError,KeyError,TypeError,ValueError) as exc:
        return dict(status='rejected',binding_valid=False,explanation='Validation evidence rejected: '+str(exc))


def attach_report(directory, report):
    """Bind to a finalized numeric artifact; numeric HDF5 is never modified here."""
    directory=Path(directory);m=read_json(directory/'overview_manifest.json')
    report=dict(report,numeric_sha256=file_hash(directory/'overview.h5'),
                recipe_sha256=digest(m['identity']),scope_sha256=digest(scope_identity(m)),scope=scope_identity(m))
    if report['numeric_sha256']!=m['numeric_sha256']:raise ValueError('Numeric artifact changed before report binding')
    path=directory/'validation_report.json';atomic_json(path,report)
    m['validation_report']=dict(path=path.name,sha256=file_hash(path))
    atomic_json(directory/'overview_manifest.json',m)
    status=evidence_status(directory,m)
    if not status['binding_valid']:raise ValueError(status['explanation'])
    return report
