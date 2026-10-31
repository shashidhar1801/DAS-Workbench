"""Independent native reader and numerical reference; no production science imports."""
from pathlib import Path
import hashlib,json,platform,struct,sys,time
import numpy as np
import h5py
from scipy.io import loadmat,whosmat

VERSION='public-direct-reference-1'


def hashfile(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        while b:=f.read(4*1024**2):h.update(b)
    return h.hexdigest()


class Direct:
    def __init__(self,path):
        self.path=Path(path);self.h=None;self.m=None
        if h5py.is_hdf5(path):self.h=h5py.File(path,'r')
        else:
            inv={k:(tuple(shape),typ) for k,shape,typ in whosmat(path)}
            assert inv['data'][1]=='double'
            self.meta=loadmat(path,variable_names=[k for k in inv if k not in ('data','info_DAS_meta_raw')],squeeze_me=True)
            # Independently walk the MAT element layout. No production parser/offset used.
            with open(path,'rb') as f:
                hdr=f.read(128);en='<' if hdr[-2:]==b'IM' else '>';offset=128
                while offset<self.path.stat().st_size:
                    f.seek(offset);typ,length=struct.unpack(en+'II',f.read(8));assert typ==14
                    end=offset+8+((length+7)//8)*8;parts=[]
                    for _ in range(3):
                        tag=f.read(8);a,b=struct.unpack(en+'II',tag)
                        if a>>16:
                            dt,n=struct.unpack(en+'HH',tag[:4]);parts.append(tag[4:4+n])
                        else:
                            parts.append(f.read(b));f.seek((-b)%8,1)
                    if parts[2]==b'data':
                        dt,nbytes=struct.unpack(en+'II',f.read(8));assert dt==9 and nbytes==np.prod(inv['data'][0])*8
                        self.m=np.memmap(path,dtype=en+'f8',mode='r',shape=inv['data'][0],offset=f.tell(),order='F');break
                    offset=end
            assert self.m is not None
    def read(self,group,a,b,ch):
        if self.m is not None:return np.array(self.m[np.asarray(ch),a:b].T,copy=True)
        d=self.h[group]
        if d.ndim==1:return d[a:b][:,None]
        axes=[v.decode() if isinstance(v,bytes) else str(v) for v in self.h['header/dimensionNames'][:]] if group=='/data' else ['time','distance']
        return d[a:b,ch] if axes[0]=='time' else d[ch,a:b].T
    def coords(self,group,key,ch):
        if self.m is not None:
            k='x1_distance_from_shore_m' if key=='Distance from shore (m)' else 'x1_position_m'
            return self.meta[k][ch]
        if group=='/data':
            h=self.h
            return h['cableSpec/sensorDistances'][:][ch] if 'cableSpec/sensorDistances' in h else h['header/channels'][:][ch]*float(h['header/dx'][()])
        return np.asarray(ch)
    def dt(self,group):
        if self.m is not None:return float(self.meta['info_sample_interval_s'])
        if group=='/data':return float(self.h['header/dt'][()])
        return 1/float(self.h[group].attrs['OutputDataRate' if group=='/DAS' else 'Sample Rate (Hz)'])
    def close(self):
        if self.h:self.h.close()
        self.m=None


def longest(mask):
    # Independent run-boundary calculation rather than a streaming state loop.
    e=np.diff(np.r_[False,mask,False].astype(np.int8));starts=np.flatnonzero(e==1);ends=np.flatnonzero(e==-1)
    return int(np.max(ends-starts,initial=0))


def validate(path,directory,control=lambda:None,progress=lambda x:None):
    began=time.perf_counter();d=Path(directory);m=json.loads((d/'manifest.json').read_text());r=json.loads((d/'recipe.json').read_text());direct=Direct(path)
    checks={};metrics=[];eps=np.finfo(float).eps
    def check(k,v):checks[k]=bool(v)
    check('numeric_digest',hashfile(d/'numeric.h5')==m['numeric_sha256']);check('recipe_digest',hashfile(d/'recipe.json')==m['recipe_sha256'])
    st=Path(path).stat();check('source_signature',st.st_size==m['source']['bytes'] and st.st_mtime_ns==m['source']['mtime_ns'])
    try:
        with h5py.File(d/'numeric.h5') as h:
            for i,entry in enumerate(m['signals']):
                control();s=entry['selection'];a,b=s['start'],s['end'];ch=np.array(s['channels']);g=h[f'signal{i}'];dt=direct.dt(s['group']);fs=1/dt;prefix=f'signal{i}';n=b-a;bn=s['time_bin'];stride=s['display_stride'];trace=None
                native_shape=direct.m.shape if direct.m is not None else direct.h[s['group']].shape
                native_dtype=direct.m.dtype if direct.m is not None else direct.h[s['group']].dtype
                check(prefix+'_native_shape',list(native_shape)==entry['metadata']['native_shape'])
                check(prefix+'_native_dtype',np.dtype(entry['metadata']['native_dtype'])==native_dtype)
                check(prefix+'_group',g.attrs['native_group']==s['group']==entry['metadata']['group'])
                check(prefix+'_dt',dt==entry['metadata']['dt']);check(prefix+'_indices',np.array_equal(g['channels'][:],ch))
                check(prefix+'_coordinate',np.array_equal(g['coordinate'][:],direct.coords(s['group'],s['coordinate'],ch)))
                check(prefix+'_display_coordinate',np.array_equal(g['waterfall_coordinate'][:],direct.coords(s['group'],s['coordinate'],ch[::stride])))
                check(prefix+'_bins',np.array_equal(g['bin_edges'][:],np.r_[np.arange(0,n,bn),n]))
                check(prefix+'_display_membership',np.array_equal(g['waterfall_channels'][:],ch[::stride]))
                exp_time=direct.meta['x2_time_s'][a:b] if direct.m is not None else np.arange(a,b)*dt
                check(prefix+'_time',np.array_equal(g['trace_time'][:],exp_time))
                for k in g['ids']:
                    ids=direct.meta['x1_relative_channel' if k=='relative ID' else 'x1_absolute_channel'][ch] if direct.m is not None else (direct.h['header/channels'][:][ch] if s['group']=='/data' else ch)
                    check(prefix+'_ids_'+k,np.array_equal(g['ids/'+k][:],ids))
                maximum=0.;worst=None;error_abs={};all_time=True;qc=True;etime=np.zeros(n,np.int64)
                tile=max(1,min(32,(32*1024**2)//(n*8)))
                for j in range(0,len(ch),tile):
                    control();raw=direct.read(s['group'],a,b,ch[j:j+tile]);arr=raw.astype(np.float64)
                    for kk,c in enumerate(ch[j:j+tile]):
                        k=j+kk;x=arr[:,kk];valid=np.isfinite(x);v=x[valid];mean=np.mean(v) if len(v) else np.nan;z=x-mean;rr=np.sqrt(np.mean((v-mean)**2)) if len(v) else np.nan
                        tol=1e-10*rr+64*eps*np.max(np.abs(v),initial=0)
                        vals={'mean':(g['mean'][k],mean),'rms':(g['rms'][k],rr)}
                        qc &= g['valid_count'][k]==len(v) and g['excluded_count'][k]==n-len(v)
                        qc &= g['minimum'][k]==np.min(v,initial=np.inf) and g['maximum'][k]==np.max(v,initial=-np.inf)
                        qc &= g['zero_count'][k]==np.count_nonzero(x==0) and g['constant'][k]==(len(v)>0 and v.min()==v.max()) and g['all_zero'][k]==(len(v)>0 and np.all(v==0))
                        if np.issubdtype(raw.dtype,np.integer):
                            lim=np.iinfo(raw.dtype);ex=(x==lim.min)|(x==lim.max);etime+=ex;qc &= g['extreme_count'][k]==ex.sum() and g['longest_extreme_run'][k]==longest(ex)
                        if k%stride==0:
                            expected=np.array([np.mean(z[t:t+bn]) if np.isfinite(z[t:t+bn]).all() else np.nan for t in range(0,n,bn)])
                            vals['waterfall']=(g['waterfall'][k//stride],expected)
                        if c==s['trace']:
                            trace=x.copy();vals['trace']=(g['trace'][:],z);check(prefix+'_raw_native_exact',np.array_equal(g['raw_trace'][:],raw[:,kk],equal_nan=True))
                        for name,(actual,expected) in vals.items():
                            actual=np.asarray(actual);expected=np.asarray(expected);same=np.array_equal(np.isfinite(actual),np.isfinite(expected));delta=np.abs(actual-expected);err=float(np.nanmax(delta)) if np.isfinite(delta).any() else 0.
                            ok=same and err<=tol;all_time &= ok;ratio=err/tol if tol>0 else (0 if err==0 else float('inf'))
                            error_abs[name]=max(error_abs.get(name,0),err)
                            if ratio>=maximum:maximum=ratio;worst=dict(channel_index=int(c),product=name,error=err,tolerance=float(tol),element=int(np.nanargmax(delta)) if np.isfinite(delta).any() else None)
                    progress(f'Reference {prefix}: {min(j+tile,len(ch))}/{len(ch)} channels')
                qc &= np.array_equal(g['extreme_time_count'][:],etime)
                check(prefix+'_quality_all',qc);check(prefix+'_time_domain_gate',all_time)
                p=entry['accepted'];ns=int(np.floor(s['window_s']*fs+.5));hop=int(np.floor(s['hop_s']*fs+.5));nf=int(s.get('nfft') or ns)
                check(prefix+'_accepted_samples',ns==p['nperseg'] and hop==p['hop'] and nf==p['nfft'])
                window=np.hanning(ns+1)[:-1];starts=np.arange(0,n-ns+1,hop);sp=g['spectral'];check(prefix+'_frame_indices',np.array_equal(sp['frame_starts'][:],starts));check(prefix+'_frequency',np.array_equal(sp['frequency'][:],np.fft.rfftfreq(nf,dt)))
                check(prefix+'_window',np.allclose(sp['window'][:],window,rtol=0,atol=4*eps))
                refs=[];valid_frames=[];max_energy=max_peak=max_parseval=0.;spectral_ok=True
                for q,t in enumerate(starts):
                    control();frame=trace[t:t+ns];valid=np.isfinite(frame).all();valid_frames.append(valid)
                    if not valid:
                        spectral_ok &= np.isnan(sp['density'][q]).all();continue
                    centred=frame-np.mean(frame);weighted=centred*window;fft=np.fft.rfft(weighted,nf);ref=np.abs(fft)**2/(fs*np.dot(window,window));ref[1:(-1 if nf%2==0 else None)]*=2;actual=sp['density'][q];power=np.dot(weighted,weighted)/np.dot(window,window);df=fs/nf
                    energy=float(ref.sum()*df);peak=float(ref.max());err=abs(actual-ref)
                    e=float(err.sum()*df/energy) if energy else float(np.max(err));pk=float(err.max()/peak) if peak else float(np.max(err));parse=float(abs(actual.sum()*df-power)/power) if power else float(abs(actual.sum()*df))
                    max_energy=max(max_energy,e);max_peak=max(max_peak,pk);max_parseval=max(max_parseval,parse)
                    spectral_ok &= (e<=1e-10 and pk<=1e-10 and parse<=1e-10) if energy else np.all(actual==0)
                    spectral_ok &= abs(float(sp['weighted_frame_power'][q])-power)<=(1e-10*power if power else 0)
                    refs.append(ref)
                check(prefix+'_valid_frames',np.array_equal(sp['frame_valid'][:],valid_frames));ref_psd=np.mean(refs,axis=0);actual=sp['psd'][:];err=abs(actual-ref_psd);energy=ref_psd.sum();peak=ref_psd.max()
                welch_e=float(err.sum()/energy) if energy else float(err.max());welch_p=float(err.max()/peak) if peak else float(err.max());check(prefix+'_spectral_gate',spectral_ok and welch_e<=1e-10 and welch_p<=1e-10)
                pressure_metric=None
                if 'pressure' in g:
                    x=trace-np.mean(trace[np.isfinite(trace)]);pw=int(np.floor(s['pressure_window_s']*fs+.5));count=len(x)//pw;ps=g['pressure'];po=[];good=[]
                    for t in range(0,count*pw,pw):
                        zz=x[t:t+pw];ok=np.isfinite(zz).all();good.append(ok);po.append(np.dot(zz,zz)/pw if ok else np.nan)
                    po=np.asarray(po)
                    with np.errstate(divide='ignore',invalid='ignore'):levels=10*np.log10(po/1e-12)
                    pos=np.isfinite(po)&(po>0);actual=ps['mean_square'][:];er=float(np.max(abs(actual[pos]-po[pos])/po[pos],initial=0));le=float(np.max(abs(ps['level_db'][:][pos]-levels[pos]),initial=0))
                    check(prefix+'_pressure',entry['metadata']['received_pressure'] and np.array_equal(ps['starts'][:],np.arange(count)*pw) and np.array_equal(ps['valid'][:],good) and er<=1e-10 and le<=1e-8 and np.all(actual[po==0]==0) and np.all(np.isneginf(ps['level_db'][:][po==0])))
                    pressure_metric=dict(power_relative=er,level_error_db=le,complete_windows=count,used_samples=int(np.sum(good)*pw),omitted_tail_samples=n-count*pw)
                # Every production decoded tile checked through this independent native access.
                payload_ok=True
                for block in entry['decoded_blocks']:
                    control();v=direct.read(block['group'],block['start'],block['end'],block['channels']);payload_ok &= hashlib.sha256(np.ascontiguousarray(v).tobytes()).hexdigest()==block['sha256']
                check(prefix+'_decoded_payloads',payload_ok)
                metrics.append(dict(group=s['group'],time_max_error_over_tolerance=maximum,time_worst_location=worst,time_absolute_errors=error_abs,
                    stft_integrated_relative=max_energy,stft_peak_relative=max_peak,parseval_relative=max_parseval,welch_integrated_relative=welch_e,welch_peak_relative=welch_p,pressure=pressure_metric))
        check('source_unchanged_after',Path(path).stat().st_size==st.st_size and Path(path).stat().st_mtime_ns==st.st_mtime_ns)
    finally:direct.close()
    return dict(status='passed' if all(checks.values()) else 'failed',checks=checks,metrics=metrics,
        **{k:m[k] for k in ('numeric_sha256','recipe_sha256','scope_sha256')},validator=dict(version=VERSION,source_sha256=hashfile(Path(sys._MEIPASS)/'validation_source/public_reference.py' if getattr(sys,'frozen',False) else __file__)),
        method='Independent native reads; direct two-pass population statistics; numpy FFT with independently assembled periodic Hann frames; every saved science array and decoded tile compared',
        thresholds=dict(time='1e-10 R_ref + 64 eps max|native x|',spectral_integrated=1e-10,spectral_peak=1e-10,parseval=1e-10,pressure_power=1e-10,pressure_db=1e-8,zero='exact'),
        environment=dict(python=sys.version,numpy=np.__version__,h5py=h5py.__version__,platform=platform.platform()),elapsed_s=time.perf_counter()-began)
