"""Bounded CPU float64 analysis for native public quantities, contract v1."""
from pathlib import Path
import json, math, os, shutil, time, uuid, hashlib
from datetime import datetime
import numpy as np
import h5py
from scipy.fft import rfft, rfftfreq
from .native import open_recording, sha, plain, TILE_BYTES

ALGORITHM='public-native-analysis-1'
BUDGET=256*1024**2


def dump(path, value):
    Path(path).write_text(json.dumps(plain(value),indent=2,allow_nan=False))


def digest(value):
    return hashlib.sha256(json.dumps(plain(value),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def accepted_samples(seconds, fs): return int(math.floor(float(seconds)*fs+.5))


def plan(signals, recipe):
    if recipe.get('algorithm',ALGORITHM)!=ALGORITHM or recipe.get('backend','cpu-float64')!='cpu-float64': raise ValueError('Unsupported public algorithm/backend')
    items=[];blocks=[]
    for spec in recipe['signals']:
        s=next(x for x in signals if x.group==spec['group'])
        a,b=int(spec['start']),int(spec['end']);ch=np.asarray(spec['channels'],dtype=int)
        if not 0<=a<b<=s.n or not len(ch) or np.any(ch<0) or np.any(ch>=s.c) or np.any(np.diff(ch)<=0): raise ValueError('Selection must be sorted unique channels and half-open native indices')
        n=b-a;stride=int(spec.get('display_stride',1));bin_n=int(spec.get('time_bin',1));trace=int(spec['trace'])
        if stride<1 or bin_n<1 or trace not in ch: raise ValueError('Invalid display/trace selection')
        if spec['coordinate'] not in s.coordinates: raise ValueError('Unknown coordinate')
        ns=accepted_samples(spec['window_s'],s.fs);hop=accepted_samples(spec['hop_s'],s.fs);nfft=int(spec.get('nfft') or ns)
        if ns<2 or hop<1 or hop>ns or ns>n or nfft<ns: raise ValueError('Window/hop/FFT exceed native support; no padded boundary frames')
        fmax=float(spec.get('fmax',s.fs/2))
        if not 0<fmax<=s.fs/2+1e-12: raise ValueError('Displayed frequency exceeds native Nyquist')
        hann=.5-.5*np.cos(2*np.pi*np.arange(ns,dtype=np.float64)/ns)
        enbw=float(s.fs*np.dot(hann,hann)/np.sum(hann)**2)
        nr=len(ch[::stride]);nb=math.ceil(n/bin_n);frames=1+(n-ns)//hop;nf=nfft//2+1
        # Streaming accumulators, two native/float tiles, one full trace, spectrum
        # output and per-frame work. Plot/Qt/export memory measured separately.
        numerical=nr*nb*8+n*42+frames*nf*8+len(ch)*160+min(n,8192)*min(len(ch),64)*40+ns*32+nfft*32
        if n>10_000_000:blocks.append('Native trace exceeds 10,000,000 samples; shorten selection explicitly')
        if nr*nb>8_000_000:blocks.append('Waterfall exceeds 8,000,000 pixels; accept a larger display bin explicitly')
        if numerical>BUDGET:blocks.append('Planned numerical buffers exceed 256 MiB; shorten selection or adjust display/spectral parameters explicitly')
        if bin_n>8192:blocks.append('Display bin exceeds bounded reduction tile8192; choose shorter selection/stride')
        if spec.get('pressure_window_s') is not None and not s.pressure:raise ValueError('Received levels require verified pressure data in pascals.')
        pw=accepted_samples(spec['pressure_window_s'],s.fs) if spec.get('pressure_window_s') is not None else None
        if pw is not None and pw<1:raise ValueError('Pressure averaging window is shorter than one sample')
        items.append(dict(group=s.group,start=a,end=b,n=n,channels=len(ch),waterfall_rows=nr,waterfall_columns=nb,waterfall_pixels=nr*nb,
                 suggested_time_bin=max(bin_n,math.ceil(n/max(1,8_000_000//nr))),native_logical_bytes=n*len(ch)*np.dtype(s.dtype).itemsize,
                 native_tile_limit=TILE_BYTES,numerical_buffer_estimate=numerical,physical_io_bytes=None,physical_io_note='Unmeasured; compression/mmap page reads differ from logical bytes',
                 nperseg=ns,hop=hop,nfft=nfft,window_actual_s=ns*s.dt,hop_actual_s=hop*s.dt,df=s.fs/nfft,
                 enbw_hz=enbw,nyquist=s.fs/2,frames=frames,pressure_window_samples=pw,
                 pressure_window_actual_s=pw*s.dt if pw else None))
    return dict(allowed=not blocks,blocking_reasons=blocks,signals=items,budget_bytes=BUDGET,backend='cpu-float64',algorithm=ALGORITHM,
                assumptions='One signal at a time; streaming ≤64 channels ×8192 samples; trace and STFT retained; GUI/export RSS measured separately')


def spectrum(x, fs, nseg, hop, nfft, control=lambda:None, sample_interval=None):
    w=.5-.5*np.cos(2*np.pi*np.arange(nseg,dtype=np.float64)/nseg)
    starts=np.arange(0,len(x)-nseg+1,hop,dtype=np.int64)
    density=np.full((len(starts),nfft//2+1),np.nan);power=np.full(len(starts),np.nan);valid=np.zeros(len(starts),bool)
    for k,a in enumerate(starts):
        control();z=x[a:a+nseg]
        if not np.isfinite(z).all(): continue
        z=(z-z.mean())*w;y=rfft(z,n=nfft)
        p=(y.real*y.real+y.imag*y.imag)/(fs*np.sum(w*w))
        p[1:-1 if nfft%2==0 else None]*=2
        density[k]=p;power[k]=np.sum(z*z)/np.sum(w*w);valid[k]=True
    if not valid.any():raise ValueError('No complete valid spectral frames')
    return dict(window=w,frame_starts=starts,frame_valid=valid,frequency=rfftfreq(nfft,1/fs if sample_interval is None else sample_interval),density=density,
                psd=np.mean(density[valid],axis=0),weighted_frame_power=power)


def pressure(x, fs, window):
    n=len(x)//window;starts=np.arange(n,dtype=np.int64)*window
    p=np.full(n,np.nan);valid=np.zeros(n,bool)
    for i,a in enumerate(starts):
        z=x[a:a+window]
        if np.isfinite(z).all(): p[i]=np.mean(z*z);valid[i]=True
    with np.errstate(divide='ignore',invalid='ignore'):level=10*np.log10(p/1e-12)
    return dict(starts=starts,mean_square=p,level_db=level,valid=valid)


def statistics(s,spec,control,progress):
    a,b=spec['start'],spec['end'];chs=np.asarray(spec['channels']);n=b-a;c=len(chs)
    stride=spec.get('display_stride',1);bn=spec.get('time_bin',1);shown=chs[::stride]
    wf=np.full((len(shown),math.ceil(n/bn)),np.nan);mu=np.empty(c);rms=np.empty(c);counts=np.zeros(c,np.int64)
    lo=np.full(c,np.inf);hi=np.full(c,-np.inf);ext=np.zeros(c,np.int64);longest=np.zeros(c,np.int64);zero=np.zeros(c,np.int64)
    extreme_time=np.zeros(n,np.int64);hashes=[];step=max(bn,(8192//bn)*bn);integer=np.issubdtype(np.dtype(s.dtype),np.integer)
    native_extrema=(np.iinfo(s.dtype).min,np.iinfo(s.dtype).max) if integer else None
    for j in range(0,c,64):
        ch=chs[j:j+64];m=len(ch);offset=None;total=np.zeros(m);cnt=np.zeros(m,np.int64);current=np.zeros(m,np.int64)
        for start in range(a,b,step):
            control();end=min(start+step,b);raw=s.read(start,end,ch);x=raw.astype(np.float64);valid=np.isfinite(x)
            hashes.append(dict(group=s.group,start=start,end=end,channels=ch.tolist(),dtype=str(raw.dtype),shape=list(raw.shape),sha256=hashlib.sha256(np.ascontiguousarray(raw).tobytes()).hexdigest()))
            if offset is None:offset=np.where(np.isfinite(x[0]),x[0],0)
            total+=np.where(valid,x-offset,0).sum(axis=0);cnt+=valid.sum(axis=0)
            lo[j:j+m]=np.minimum(lo[j:j+m],np.min(np.where(valid,x,np.inf),axis=0));hi[j:j+m]=np.maximum(hi[j:j+m],np.max(np.where(valid,x,-np.inf),axis=0));zero[j:j+m]+=(x==0).sum(axis=0)
            if integer:
                e=(raw==native_extrema[0])|(raw==native_extrema[1]);ext[j:j+m]+=e.sum(axis=0);extreme_time[start-a:end-a]+=e.sum(axis=1)
                # Time run lengths include either integer extreme, without masking them.
                for row in e:
                    current=np.where(row,current+1,0);longest[j:j+m]=np.maximum(longest[j:j+m],current)
        with np.errstate(invalid='ignore',divide='ignore'):mean=offset+total/cnt
        mu[j:j+m]=mean;counts[j:j+m]=cnt;squares=np.zeros(m)
        selected=[(k,int(np.flatnonzero(shown==v)[0])) for k,v in enumerate(ch) if v in shown]
        for start in range(a,b,step):
            control();end=min(start+step,b);x=s.read(start,end,ch).astype(np.float64);z=x-mean;valid=np.isfinite(z)
            squares+=np.where(valid,z*z,0).sum(axis=0)
            for k,row in selected:
                vals=z[:,k];edges=np.arange(0,len(vals),bn);sums=np.add.reduceat(np.where(np.isfinite(vals),vals,0),edges);length=np.minimum(bn,len(vals)-edges)
                ok=np.add.reduceat(np.isfinite(vals).astype(np.int64),edges)==length
                wf[row,(start-a)//bn:(start-a)//bn+len(edges)]=np.where(ok,sums/length,np.nan)
        with np.errstate(divide='ignore',invalid='ignore'):rms[j:j+m]=np.sqrt(squares/cnt)
        progress(f'{s.group}: statistics {min(j+64,c)}/{c} channels')
    if not np.any(counts):raise ValueError('All selected samples invalid')
    tr=int(spec['trace']);trace=np.empty(n,np.float64);rawtrace=np.empty(n,dtype=s.dtype)
    for start in range(a,b,8192):
        control();end=min(start+8192,b);rawtrace[start-a:end-a]=s.read(start,end,[tr])[:,0]
    trace[:]=rawtrace.astype(float)-mu[np.flatnonzero(chs==tr)[0]]
    return dict(channels=chs,mean=mu,rms=rms,valid_count=counts,excluded_count=n-counts,minimum=lo,maximum=hi,
                extreme_count=ext,longest_extreme_run=longest,extreme_time_count=extreme_time,zero_count=zero,
                constant=(lo==hi)&(counts>0),all_zero=(zero==counts)&(counts>0),waterfall=wf,waterfall_channels=shown,
                bin_edges=np.r_[np.arange(0,n,bn),n],trace=trace,raw_trace=rawtrace,trace_time=s.times(a,b),decoded_hashes=hashes,
                native_extrema=native_extrema)


def analyse(path,recipe,destination,control=lambda:None,progress=lambda x:None):
    start=time.perf_counter();dest=Path(destination)
    if dest.exists():raise FileExistsError(dest)
    signals=open_recording(path);p=plan(signals,recipe)
    if not p['allowed']:raise ValueError('; '.join(p['blocking_reasons']))
    temp=dest.with_name('.'+dest.name+'.'+uuid.uuid4().hex);temp.mkdir(parents=True)
    try:
        source=signals[0].source
        if source['sha256'] is None:source['sha256']=sha(path);source['integrity']='Full source SHA256 computed for this analysis'
        from .public_selection import qualify_review
        recipe=qualify_review(recipe,source)
        if recipe['event_review_status']=='unreviewed selection' and recipe.get('prior_event_review'):
            recipe['title']='Native recording | unreviewed selection'
            recipe['subtitle']=signals[0].quantity+' ['+signals[0].unit+']'
        manifest=dict(schema=1,algorithm=ALGORITHM,app_version='0.3.4',backend='cpu-float64',source=source,signals=[],plan=p,
                      statuses=dict(source_integrity=source['integrity'],schema='validated native profile',quantity='See each signal',event_review=recipe.get('event_review','Not supplied'),numerical_validation='not supplied'))
        with h5py.File(temp/'numeric.h5','w') as h:
            h.attrs['algorithm']=ALGORITHM
            for num,(spec,planned) in enumerate(zip(recipe['signals'],p['signals'])):
                control();s=next(x for x in signals if x.group==spec['group']);out=statistics(s,spec,control,progress)
                sp=spectrum(out['raw_trace'].astype(float),s.fs,planned['nperseg'],planned['hop'],planned['nfft'],control,sample_interval=s.dt)
                g=h.create_group(f'signal{num}');g.attrs['native_group']=s.group;g.attrs['unit']=s.unit
                for k,v in out.items():
                    if k not in ('decoded_hashes','native_extrema'):g.create_dataset(k,data=v,compression='gzip' if np.asarray(v).size>1024 else None)
                coord=s.coordinates[spec['coordinate']];g.create_dataset('coordinate',data=coord[out['channels']]);g.create_dataset('waterfall_coordinate',data=coord[out['waterfall_channels']])
                for k,v in s.channels.items():g.create_dataset('ids/'+k,data=v[out['channels']])
                for k,v in sp.items():g.create_dataset('spectral/'+k,data=v,compression='gzip' if np.asarray(v).size>1024 else None)
                if planned['pressure_window_samples']:
                    for k,v in pressure(out['trace'],s.fs,planned['pressure_window_samples']).items():g.create_dataset('pressure/'+k,data=v)
                manifest['signals'].append(dict(metadata=s.summary(),selection=spec,accepted=planned,decoded_blocks=out['decoded_hashes'],
                      max_native_read_bytes=s.max_read_bytes,quality_summary=dict(finite_samples=int(out['valid_count'].sum()),excluded=int(out['excluded_count'].sum()),
                      integer_extreme_count=int(out['extreme_count'].sum()),channels_with_extrema=int((out['extreme_count']>0).sum()),
                      max_extreme_run=int(out['longest_extreme_run'].max()),constant_channels=int(out['constant'].sum()),all_zero_channels=int(out['all_zero'].sum()),
                      extrema_policy='Retained; no sentinel assumption',spectral_valid_frames=int(sp['frame_valid'].sum()),spectral_rejected_frames=int((~sp['frame_valid']).sum()))))
                s.check();del out,sp
        control()
        for s in signals:s.check()
        dump(temp/'recipe.json',recipe);manifest['recipe_sha256']=sha(temp/'recipe.json');manifest['numeric_sha256']=sha(temp/'numeric.h5')
        manifest['scope_sha256']=digest(dict(source=source,recipe=recipe,signals=manifest['signals'],backend=manifest['backend'],algorithm=ALGORITHM))
        manifest['elapsed_analysis_s']=time.perf_counter()-start;dump(temp/'manifest.json',manifest)
        dump(temp/'root_map_template.json',{'source_root':'RELINK_SOURCE_ROOT','relative_path':source['relative_path']})
        control();os.replace(temp,dest);return manifest
    finally:
        if temp.exists():shutil.rmtree(temp)


def load_result(directory):
    d=Path(directory);m=json.loads((d/'manifest.json').read_text());r=json.loads((d/'recipe.json').read_text())
    if sha(d/'numeric.h5')!=m['numeric_sha256'] or sha(d/'recipe.json')!=m['recipe_sha256']:raise ValueError('Numeric or recipe hash mismatch')
    scope=digest(dict(source=m['source'],recipe=r,signals=m['signals'],backend=m['backend'],algorithm=m['algorithm']))
    if scope!=m['scope_sha256']:raise ValueError('Result scope changed')
    return m,r


def evidence(directory):
    d=Path(directory)
    try:
        m,r=load_result(d);v=m.get('validation')
        if not v:return dict(status='not supplied')
        if Path(v['file']).name!=v['file']:raise ValueError('Unsafe sidecar path')
        if sha(d/v['file'])!=v['sha256']:raise ValueError('Sidecar hash mismatch')
        report=json.loads((d/v['file']).read_text())
        for k in ['numeric_sha256','recipe_sha256','scope_sha256']:
            if report[k]!=m[k]:raise ValueError(k+' mismatch')
        if report['status']=='passed' and (not report['checks'] or not all(report['checks'].values())):raise ValueError('Report has failed checks')
        return dict(status=report['status'],report=report)
    except Exception as e:return dict(status='rejected',reason=str(e))


def attach(directory,report):
    d=Path(directory);m,r=load_result(d)
    for k in ['numeric_sha256','recipe_sha256','scope_sha256']:
        if report[k]!=m[k]:raise ValueError('Validation does not match this result')
    dump(d/'validation_report.json',report);m['validation']=dict(file='validation_report.json',sha256=sha(d/'validation_report.json'));dump(d/'manifest.json',m)
