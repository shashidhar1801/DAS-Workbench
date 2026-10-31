"""Independent two-pass int64-offset reference; no production reader/reducer.

Reads HDF5 directly in <=510-channel int32 stripes. Only one channel at a
 time is converted to float64. Thresholds below are fixed before execution.
"""
from contextlib import ExitStack
from pathlib import Path
import hashlib
import platform
import time
import sys
import numpy as np
import h5py
from das_workbench.contracts import read_json, file_hash

VERSION='direct-int64-two-pass-1'
ABS_COUNTS=1e-6
REL_RMS=1e-10
RAW='Acquisition/Raw[0]'


def validate(directory,roots):
    start_clock=time.perf_counter()
    directory=Path(directory);m=read_json(directory/'overview_manifest.json')
    n=m['selection']['count'];nc=m['estimate']['all_channels']
    first=m['selection']['first_us'];end=m['selection']['end_exclusive_us'];tick=round(1e6/m['fs'])
    scale=m['phase_scale'];settings=m['settings'];bin_n=settings['time_bin_samples'];stride=settings['channel_stride']
    rows=np.arange(0,nc,stride,dtype=np.int64);bins=np.arange(0,n,bin_n,dtype=np.int64)
    edges=np.r_[bins,n];checks={};sources=[];segments=[];signatures=[]
    def exact(name,actual,expected):
        passed=bool(np.array_equal(actual,expected))
        checks[name]=dict(passed=passed,method='exact array/value equality')
        if not passed:raise AssertionError('Exact structural failure: '+name)
    maxima={k:dict(passed=True,max_absolute_error_rad=0.,max_error_location=None,threshold_at_max_error_rad=None,
                   max_threshold_fraction=0.,worst_fraction_location=None) for k in ('mean','rms','trace','waterfall','trace_residual_mean')}
    def compare(name,actual,expected,tau,channel):
        actual=np.asarray(actual);expected=np.asarray(expected)
        if not np.array_equal(np.isnan(actual),np.isnan(expected)):raise AssertionError(name+' NaN mask differs')
        mask=np.isfinite(expected)
        if not np.array_equal(np.isfinite(actual),mask):raise AssertionError(name+' finite mask differs')
        delta=np.abs(actual[mask]-expected[mask])
        err=float(delta.max()) if delta.size else 0.
        indices=np.flatnonzero(mask)
        location=dict(channel=int(channel),index=int(indices[int(np.argmax(delta))]) if delta.size else None)
        item=maxima[name]
        if item['max_error_location'] is None or err>item['max_absolute_error_rad']:
            item.update(max_absolute_error_rad=err,max_error_location=location,threshold_at_max_error_rad=float(tau))
        if item['worst_fraction_location'] is None or err/tau>item['max_threshold_fraction']:
            item.update(max_threshold_fraction=err/tau,worst_fraction_location=location)
        item['passed'] &= err<=tau
    with ExitStack() as stack:
        status=[];timestamps=[];counters=[];counter_step=None
        offset=0
        for source in m['identity']['run']['files']:
            path=Path(roots[source['root_id']])/source['relative_path'];st=path.stat()
            exact('source_signature_'+source['id'],[st.st_size,st.st_mtime_ns],[source['size'],source['mtime_ns']])
            signatures.append((path,st.st_size,st.st_mtime_ns))
            f=stack.enter_context(h5py.File(path,'r',rdcc_nbytes=8*1024**2));r=f[RAW];a=f['Acquisition']
            t=r['RawDataTime'][:]
            exact('time_grid_'+source['id'],t,source['first_us']+np.arange(source['samples'],dtype=np.int64)*tick)
            lo=int(np.searchsorted(t,first));hi=int(np.searchsorted(t,end))
            d=r['RawData'];axes=[x.decode() if isinstance(x,bytes) else x for x in d.attrs['Dimensions']]
            exact('axes_'+source['id'],axes,source['axes'])
            exact('rate_'+source['id'],float(r.attrs['OutputDataRate']),m['fs'])
            unit=r.attrs['RawDataUnit'];unit=unit.decode() if isinstance(unit,bytes) else unit
            exact('phase_scale_'+source['id'],2*np.pi/65536 if unit=='rad * 2PI/2^16' else np.nan,scale)
            exact('distance_metadata_'+source['id'],[a.attrs['SpatialSamplingInterval'],r.attrs['StartLocusIndex']],[m['spacing_m'],m['start_locus']])
            reason=np.zeros(hi-lo,dtype=np.uint16);custom=r.get('Custom')
            vals={key: custom[key][lo:hi] if custom is not None and key in custom else None for key in ('SampleCount','GpBits','GpsStatus','PpsOffset')}
            if any(v is None for v in vals.values()) or source['counter_step'] is None:reason|=32
            gp,gps=vals['GpBits'],vals['GpsStatus']
            if gp is None:reason|=32
            else:
                reason[(gp&2)!=0]|=1;reason[(gp&1)!=0]|=4;reason[(gp&252)!=0]|=32
            if gps is None:reason|=32
            else:
                reason[(gps&4)!=0]|=8;reason[(gps&3)!=3]|=16;reason[(gps&248)!=0]|=32
            compromised=a['Custom'].attrs.get('Data Compromised')
            if compromised is None:reason|=32
            elif compromised:reason|=2
            if vals['SampleCount'] is not None and source['counter_step'] is not None:
                exact('counter_grid_'+source['id'],np.diff(custom['SampleCount'][:]),np.full(source['samples']-1,source['counter_step']))
                counters.append(vals['SampleCount']);counter_step=source['counter_step']
            timestamps.append(t[lo:hi]);status.append(reason)
            segments.append((d,axes,lo,hi,offset));offset+=hi-lo
            sources.append(dict(id=source['id'],root_id=source['root_id'],relative_path=source['relative_path'],
                                file_sample_bounds_half_open=[lo,hi],selection_sample_bounds_half_open=[offset-(hi-lo),offset],
                                first_us=int(t[lo]),end_exclusive_us=int(t[hi-1])+tick,
                                channels_half_open=[0,nc],signature='size and mtime_ns; not a whole-file content hash'))
        reasons=np.concatenate(status);valid=(reasons&(63 if settings['strict_quality'] else 3))==0
        exact('selected_integer_timestamps',np.concatenate(timestamps),first+np.arange(n,dtype=np.int64)*tick)
        if len(counters)==len(segments):exact('counter_across_joins',np.diff(np.concatenate(counters)),np.full(n-1,counter_step))
        exact('native_sample_count',offset,n)
        exact('last_and_exclusive_time',[m['selection']['last_us'],end],[first+(n-1)*tick,first+n*tick])
        output=stack.enter_context(h5py.File(directory/'overview.h5','r'))
        exact('channels',output['channels'][:],np.arange(nc))
        exact('distance_expression',output['distance_m'][:],(np.arange(nc)+m['start_locus'])*m['spacing_m'])
        exact('waterfall_channels',output['waterfall/channels'][:],rows)
        exact('waterfall_edges',output['waterfall/time_edges_s'][:],edges/m['fs'])
        exact('waterfall_shape',output['waterfall/phase_rad'].shape,[len(rows),len(bins)])
        exact('trace_shape',output['trace/phase_rad'].shape,[n])
        exact('trace_time_attributes',[output['trace/phase_rad'].attrs['first_sample_utc_us'],output['trace/phase_rad'].attrs['sample_interval_us']],[first,tick])
        exact('trace_channel',output['trace/phase_rad'].attrs['channel'],settings['trace_channel'])
        exact('quality_reasons',output['quality/reasons'][:],reasons)
        exact('quality_exclusion_mask',output['quality/reasons'].attrs['exclusion_bit_mask'],63 if settings['strict_quality'] else 3)
        exact('valid_counts',output['valid_sample_count'][:],np.full(nc,valid.sum(),dtype=np.int64))
        exact('manifest_quality_counts',[m['quality']['valid_samples'],m['quality']['excluded_samples'],m['quality']['total_samples']],[int(valid.sum()),int((~valid).sum()),n])
        rms_ref=np.zeros(nc);means=output['channel_mean_rad'][:];rms=output['rms_rad'][:]
        block_hashes=0;max_int32_stripe_bytes=0
        start_sample=m['selection']['start_sample']
        for ca in range(0,nc,510):
            cb=min(nc,ca+510);raw=np.empty((cb-ca,n),dtype=np.int32)
            max_int32_stripe_bytes=max(max_int32_stripe_bytes,raw.nbytes)
            for ds,axes,lo,hi,at in segments:
                raw[:,at:at+hi-lo]=ds[ca:cb,lo:hi] if axes==['locus','time'] else ds[lo:hi,ca:cb].T
            for block in m['decoded_blocks']:
                if ca <= block['channels'][0] < block['channels'][1] <= cb:
                    a,b=[i-start_sample for i in block['samples']]
                    actual=hashlib.sha256(raw[block['channels'][0]-ca:block['channels'][1]-ca,a:b].astype('<i4',copy=False).tobytes()).hexdigest()
                    if actual!=block['sha256_int32_le_c_order']:raise AssertionError('decoded payload hash differs')
                    block_hashes+=1
            for local,ch in enumerate(range(ca,cb)):
                reference=int(raw[local,0])
                shifted=(raw[local].astype(np.int64)-reference).astype(np.float64)
                values=shifted[valid]
                mu=float(np.mean(values));centred=values-mu
                R=float(np.sqrt(np.sum(centred*centred)/len(values))*scale);rms_ref[ch]=R
                tau=abs(scale)*ABS_COUNTS+REL_RMS*R
                compare('mean',means[ch],(reference+mu)*scale,tau,ch)
                compare('rms',rms[ch],R,tau,ch)
                if ch==settings['trace_channel']:
                    reference_trace=(shifted-mu)*scale;reference_trace[~valid]=np.nan
                    actual=output['trace/phase_rad'][:]
                    compare('trace',actual,reference_trace,tau,ch)
                    compare('trace_residual_mean',np.mean(actual[valid]),0.,tau,ch)
                if ch%stride==0:
                    shifted[~valid]=np.nan
                    expected=(np.add.reduceat(shifted,bins)/np.diff(edges)-mu)*scale
                    compare('waterfall',output['waterfall/phase_rad'][ch//stride],expected,tau,ch)
        exact('decoded_blocks_checked',block_hashes,len(m['decoded_blocks']))
        exact('output_numeric_digest',file_hash(directory/'overview.h5'),m['numeric_sha256'])
        for path,size,mtime in signatures:
            st=path.stat();exact('source_unchanged_after_'+path.name,[st.st_size,st.st_mtime_ns],[size,mtime])
    checks.update(maxima)
    checks['masks_all_checked']=dict(passed=True,method='Every trace sample and every displayed row compared for identical NaN and finite masks')
    passed=all(c['passed'] for c in checks.values())
    return dict(status='passed' if passed else 'failed',explanation='Independent direct-HDF5 comparison covers every channel mean/RMS, the complete native trace and every saved waterfall bin in this exact selection. Engineering agreement, not physical calibration.',
        validator=dict(version=VERSION,source_sha256=file_hash(__file__)),
        method='Direct HDF5; <=510-channel int32 stripes; per-channel int64 subtraction of first integer count before float64 conversion; direct two-pass valid mean and population RMS. No production RunReader or Chan reduction.',
        environment=dict(python=sys.version.split()[0],numpy=np.__version__,h5py=h5py.__version__,platform=platform.platform(),machine=platform.machine()),
        thresholds=dict(formula='tau_c = abs(phase_scale)*1e-6 + 1e-10*reference_native_centred_RMS_c',absolute_counts=ABS_COUNTS,relative_native_rms=REL_RMS,absolute_rad=abs(scale)*ABS_COUNTS,declared_before_execution=True,
                        minimum_tau_rad=float(np.min(abs(scale)*ABS_COUNTS+REL_RMS*rms_ref)),maximum_tau_rad=float(np.max(abs(scale)*ABS_COUNTS+REL_RMS*rms_ref))),
        checks=checks,source_selections=sources,decoded_blocks_independently_verified=block_hashes,
        coverage=dict(channels=nc,native_samples_per_channel=n,trace_samples=n,waterfall_shape=[len(rows),len(bins)],file_join_offsets_samples=[s[4] for s in segments[1:]],final_bin_samples=int(np.diff(edges)[-1]),mean_scope='entire selected valid native interval; no file/folder reset'),
        reference_buffers=dict(max_int32_stripe_bytes=max_int32_stripe_bytes,arithmetic='one channel at a time; no full-stripe float64 copy',includes_io_temporary=False),
        wall_seconds=time.perf_counter()-start_clock)
