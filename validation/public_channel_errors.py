"""Supplement the scoped GUI reference report with per-channel time-domain errors.
Uses the independent Direct reader, never the production adapter or reducers.
"""
from pathlib import Path
import csv,json,sys
import h5py,numpy as np
from .public_reference import Direct,hashfile


def main(root,data_root):
    b=Path(root);results={}
    for case in ['F01','F02','F03','F04','S01']:
        d=b/'examples'/case;m=json.loads((d/'manifest.json').read_text());p=Path(data_root)/m['source']['relative_path'];direct=Direct(p);rows=[]
        assert hashfile(d/'numeric.h5')==m['numeric_sha256']
        try:
            with h5py.File(d/'numeric.h5') as h:
                for i,e in enumerate(m['signals']):
                    s=e['selection'];g=h[f'signal{i}'];a,z=s['start'],s['end'];ch=s['channels'];bn=s['time_bin'];stride=s['display_stride'];tile=max(1,min(32,(32*1024**2)//((z-a)*8)))
                    for j in range(0,len(ch),tile):
                        raw=direct.read(s['group'],a,z,ch[j:j+tile]).astype(np.float64)
                        for kk,c in enumerate(ch[j:j+tile]):
                            k=j+kk;x=raw[:,kk];v=x[np.isfinite(x)];mean=np.mean(v) if len(v) else np.nan;centred=x-mean;rms=np.sqrt(np.mean((v-mean)**2)) if len(v) else np.nan
                            tau=1e-10*rms+64*np.finfo(float).eps*np.max(np.abs(v),initial=0)
                            values={'mean':(g['mean'][k],mean),'rms':(g['rms'][k],rms)}
                            if k%stride==0:values['waterfall']=(g['waterfall'][k//stride],np.array([np.mean(centred[t:t+bn]) if np.isfinite(centred[t:t+bn]).all() else np.nan for t in range(0,z-a,bn)]))
                            if c==s['trace']:values['trace']=(g['trace'][:],centred)
                            for name,(actual,expected) in values.items():
                                actual=np.asarray(actual);expected=np.asarray(expected);delta=np.abs(actual-expected);finite=np.isfinite(delta);err=float(np.nanmax(delta)) if finite.any() else 0.;ratio=err/tau if tau>0 else (0. if err==0 else float('inf'))
                                passed=bool(np.array_equal(np.isfinite(actual),np.isfinite(expected)) and err<=tau)
                                assert passed,(case,i,c,name,err,tau)
                                rows.append(dict(signal=i,group=s['group'],channel_index=c,product=name,reference_RMS=rms,tolerance=tau,max_abs_error=err,error_over_tolerance=ratio,worst_element=int(np.nanargmax(delta)) if finite.any() else '',passed=passed))
        finally:direct.close()
        out=b/'reports'/f'{case}_per_channel_errors.csv'
        with out.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        results[case]=dict(status='passed',rows=len(rows),worst=max(rows,key=lambda r:r['error_over_tolerance']),source_sha256=m['source']['sha256'],numeric_sha256=m['numeric_sha256'],recipe_sha256=m['recipe_sha256'],scope_sha256=m['scope_sha256'],csv=out.name,csv_sha256=hashfile(out))
    report=dict(status='passed',method='Independent direct two-pass values, all selected channels, every saved time product; same frozen tau; supplementary to GUI-bound validation',source_sha256=hashfile(__file__),independent_reader_sha256=hashfile(Path(__file__).with_name('public_reference.py')),cases=results)
    (b/'reports/per_channel_error_audit.json').write_text(json.dumps(report,indent=2));print('All per-channel time-domain gates passed.')

if __name__=='__main__':main(*sys.argv[1:])
