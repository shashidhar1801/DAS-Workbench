"""Native-header mapping and small-selection memory observations, without plots."""
from pathlib import Path
from datetime import datetime
from decimal import Decimal
import sys,json,time,resource
import psutil,h5py,numpy as np
from scipy.io import loadmat,whosmat
from das_workbench.native import open_recording,plain
from das_workbench.public_engine import dump


def main(root,study):
    root=Path(root);b=Path(study);config=json.loads((b/'local/final_config.json').read_text());results={}
    for case in config['cases']:
        path=Path(case['path']);signals=open_recording(path);checks={};summaries=[s.summary() for s in signals]
        if h5py.is_hdf5(path):
            with h5py.File(path) as f:
                for s in signals:
                    d=f[s.group];checks[s.group+'_shape']=tuple(d.shape)==s.shape;checks[s.group+'_dtype']=d.dtype==np.dtype(s.dtype)
                    if s.group=='/data':
                        h=f['header'];axis=tuple('channel' if (v.decode() if isinstance(v,bytes) else v)=='distance' else 'time' for v in h['dimensionNames'][:]);checks['axes']=s.axes==axis
                        checks['ids']=np.array_equal(s.channels['native ID'],h['channels'][:]);checks['native_dt']=s.dt==float(h['dt'][()]);checks['gauge']=s.metadata['gaugeLength']==float(h['gaugeLength'][()])
                        checks['origin']=(datetime.fromisoformat(s.origin).timestamp()==float(h['time'][()]))
                    else:
                        attrs=d.attrs;start='PartStartTime' if s.group=='/DAS' else 'Start Time';end='PartEndTime' if s.group=='/DAS' else 'End Time';v=lambda k:attrs[k].decode() if isinstance(attrs[k],bytes) else str(attrs[k]);checks[s.group+'_origin']=s.origin==v(start)
                        checks[s.group+'_end_exclusive']=abs((datetime.fromisoformat(v(end))-datetime.fromisoformat(v(start))).total_seconds()-s.n*s.dt)<1e-9
                        checks[s.group+'_pressure_eligibility']=s.pressure==(s.group=='/Recorder-C' and v('Signal')=='Sound Pressure' and v('Unit')=='Pa' and s.source['sha256']=='ccc1e437ab35912018c3de1a1fed5ef56789095dce515bd4658a45ea940a110d')
        else:
            vars=dict((k,(shape,typ)) for k,shape,typ in whosmat(path));meta=loadmat(path,variable_names=['x1_relative_channel','x1_absolute_channel','x1_position_m','x1_distance_from_shore_m','x2_time_s','info_sample_interval_s','info_GL_m'],squeeze_me=True);s=signals[0]
            checks['native_shape']=tuple(vars['data'][0])==s.shape;checks['axes']=s.axes==('channel','time');checks['native_dt']=s.dt==float(meta['info_sample_interval_s']);checks['time_vector']=np.array_equal(s.time_vector,meta['x2_time_s'])
            checks['shore_vector']=np.array_equal(s.coordinates['Distance from shore (m)'],meta['x1_distance_from_shore_m']);checks['interrogator_vector']=np.array_equal(s.coordinates['Distance from interrogator (m)'],meta['x1_position_m'])
            checks['distinct_origins']=not np.array_equal(s.coordinates['Distance from shore (m)'],s.coordinates['Distance from interrogator (m)']);checks['apex_excluded']=s.coordinates['Distance from shore (m)'].max()<25100
            before=psutil.Process().memory_info().rss;t=time.perf_counter();x=s.read(1000,9192,[0]);elapsed=time.perf_counter()-t;after=psutil.Process().memory_info().rss;peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            results['small_mat_observation']=dict(native_read_bytes=x.nbytes,max_native_read_bytes=s.max_read_bytes,source_matrix_bytes=int(np.prod(s.shape))*8,
                selected_samples=8192,selected_channels=1,elapsed_s=elapsed,rss_before=before,rss_after=after,process_lifetime_peak_RSS_bytes=peak,
                method='psutil before/after; macOS getrusage lifetime peak includes interpreter/imports; mmap pages included, physical disk IO unmeasured',full_matrix_not_materialized=peak<int(np.prod(s.shape))*8)
            checks['small_read_not_full_matrix']=results['small_mat_observation']['full_matrix_not_materialized']
        assert all(checks.values()),checks;results[case['id']]=dict(passed=True,checks=checks,native_summaries=summaries)
    m=json.loads((b/'examples/F04/manifest.json').read_text());starts=[];ends=[]
    for s in m['signals']:
        origin=datetime.fromisoformat(s['metadata']['origin']);sel=s['selection'];dt=Decimal(str(s['metadata']['dt']));starts.append((origin,Decimal(sel['start'])*dt));ends.append((origin,Decimal(sel['end'])*dt))
    origin=starts[0][0]
    def relative(item):
        delta=item[0]-origin;us=(delta.days*86400+delta.seconds)*1000000+delta.microseconds;return Decimal(us)/1000000+item[1]
    assert len(set(map(relative,starts)))==1 and len(set(map(relative,ends)))==1
    results['lake_alignment']=dict(passed=True,method='Independent exact microsecond origin differences + Decimal native dt × sample indices',start_seconds_from_DAS_origin=str(relative(starts[0])),end_seconds_from_DAS_origin=str(relative(ends[0])),groups=[s['metadata']['group'] for s in m['signals']],native_indices=[[s['selection']['start'],s['selection']['end']] for s in m['signals']],resampling=False,signal_shift=False)
    dump(b/'reports/native_schema_and_bounds.json',results)


if __name__=='__main__':main(*sys.argv[1:])
