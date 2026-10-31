import json
from pathlib import Path
import numpy as np
import h5py
import pytest
from scipy.io import savemat
from das_workbench.native import open_recording,mat_numeric_payload,TILE_BYTES
from das_workbench.public_engine import analyse,plan,spectrum,pressure,attach,evidence
from validation.public_reference import validate


def asn(path,transpose=False,invalid=False):
    n=513;dt=.01375;t=np.arange(n)*dt;x=np.stack([np.zeros(n),np.full(n,7),3+np.sin(2*np.pi*7*t),np.cos(2*np.pi*11*t)],axis=1).astype('f4')
    if invalid:x[101,2]=np.nan
    with h5py.File(path,'w') as f:
        f['data']=x.T if transpose else x
        h=f.create_group('header')
        for k,v in dict(dt=dt,dx=1.0213,time=1695373984.44,gaugeLength=1.02,name='Phase rate per distance',unit='rad/(s*m)',dataScale=1.,sensitivities=[[12.]],sensitivityUnits=['rad/(strain*m)'],channels=np.arange(4)*4,dimensionNames=['distance','time'] if transpose else ['time','distance']).items():h[k]=v
    return x


def recipe(s):
    return dict(algorithm='public-native-analysis-1',backend='cpu-float64',title='Analytic native',signals=[dict(group=s.group,start=3,end=s.n-2,channels=list(range(s.c)),trace=2,coordinate=next(iter(s.coordinates)),window_s=.5,hop_s=.2,nfft=41,fmax=30,time_bin=7,display_stride=1)])


@pytest.mark.parametrize('transpose,invalid',[(False,False),(True,True)])
def test_independent_all_outputs(tmp_path,transpose,invalid):
    path=tmp_path/'source.h5';x=asn(path,transpose,invalid);s=open_recording(path)[0];assert np.array_equal(s.read(0,30,[0,2]),x[:30,[0,2]],equal_nan=True)
    r=recipe(s);out=tmp_path/'result';analyse(path,r,out);report=validate(path,out);assert report['status']=='passed',report
    attach(out,report);assert evidence(out)['status']=='passed'
    with h5py.File(out/'numeric.h5') as f:assert np.all(f['signal0/rms'][:2]==0)
    rr=json.loads((out/'recipe.json').read_text());rr['title']='changed';(out/'recipe.json').write_text(json.dumps(rr));assert evidence(out)['status']=='rejected'


def test_mat_vectors_bounded_and_wrong_format(tmp_path):
    x=np.arange(300,dtype=float).reshape(3,100);p=tmp_path/'data.mat';dt=.001759922562478001
    vals=dict(data=x,x1_absolute_channel=np.array([20,24,28]),x1_relative_channel=np.array([5,6,7]),x1_position_m=np.array([20.3,24.4,28.5]),x1_distance_from_shore_m=np.array([10.11,14.2,18.28]),x2_time_s=np.arange(100)*dt,info_sample_interval_s=dt,info_sampling_frequency_Hz=1/dt,info_GL_m=8.17,info_SSI_m=1.0213,info_timestamp='20200627 19:22:55',info_units=np.array(['nanostrain','second','meter']))
    savemat(p,vals);s=open_recording(p)[0];assert np.array_equal(s.read(4,18,[0,2]),x[[0,2],4:18].T);assert np.array_equal(s.coordinates['Distance from shore (m)'],vals['x1_distance_from_shore_m'])
    assert s.max_read_bytes==14*2*8;assert mat_numeric_payload(p)['payload_bytes']==x.nbytes
    vals['x2_time_s'][50:]+=.1;savemat(tmp_path/'gap.mat',vals)
    with pytest.raises(ValueError,match='Nonuniform'):open_recording(tmp_path/'gap.mat')
    savemat(tmp_path/'compressed.mat',vals,do_compression=True)
    with pytest.raises(ValueError,match='Compressed'):open_recording(tmp_path/'compressed.mat')


@pytest.mark.parametrize('nfft',[31,32,40])
def test_spectrum_direct_dft_and_pressure_scaling(nfft):
    n=31;x=2+np.sin(2*np.pi*np.arange(n)*3/n);got=spectrum(x,100,n,n,nfft);w=.5-.5*np.cos(2*np.pi*np.arange(n)/n);z=(x-x.mean())*w
    ft=np.exp(-2j*np.pi*np.outer(np.arange(nfft//2+1),np.arange(n))/nfft)@z;ref=abs(ft)**2/(100*np.dot(w,w));ref[1:-1 if nfft%2==0 else None]*=2
    assert np.sum(abs(got['psd']-ref))/np.sum(ref)<1e-10
    assert np.all(spectrum(np.zeros(n),100,n,n,nfft)['psd']==0)
    assert np.all(spectrum(np.ones(n)*7,100,n,n,nfft)['psd']==0)
    p=pressure(x-x.mean(),100,10);scaled=pressure(3*(x-x.mean()),100,10)
    np.testing.assert_allclose(scaled['mean_square'],p['mean_square']*9,rtol=1e-14)
    np.testing.assert_allclose(scaled['level_db']-p['level_db'],20*np.log10(3),rtol=0,atol=1e-12)
    z=pressure(np.zeros(31),100,10);assert np.all(z['mean_square']==0) and np.isneginf(z['level_db']).all()
    xx=np.arange(60,dtype=float);xx[12]=np.nan;sp=spectrum(xx,100,20,10,21);assert sp['frame_valid'].tolist()==[False,False,True,True,True]


def test_planner_source_change_stop_and_group_protection(tmp_path):
    path=tmp_path/'source.h5';asn(path);s=open_recording(path)[0];r=recipe(s)
    r['signals'][0]['pressure_window_s']=1
    with pytest.raises(ValueError,match='verified pressure'):plan([s],r)
    del r['signals'][0]['pressure_window_s'];r['signals'][0]['window_s']=100
    with pytest.raises(ValueError,match='native support'):plan([s],r)
    r=recipe(s)
    with pytest.raises(InterruptedError):analyse(path,r,tmp_path/'stopped',control=lambda:(_ for _ in ()).throw(InterruptedError()))
    assert not (tmp_path/'stopped').exists() and not list(tmp_path.glob('.stopped.*'))
    path.touch()
    with pytest.raises(ValueError,match='changed'):s.read(0,10,[0])


def test_extrema_are_retained(tmp_path):
    p=tmp_path/'lake.h5'
    with h5py.File(p,'w') as f:
        x=np.zeros((1000,3),dtype='i2');x[:3,2]=-32768;x[10,1]=32767;d=f.create_dataset('DAS',data=x)
        for k,v in dict(OutputDataRate=100.,PartStartTime='2022-01-01T00:00:00+00:00',PartEndTime='2022-01-01T00:00:10+00:00',RawDataUnit='(nm/m)/s',AmpScaling=3.54).items():d.attrs[k]=v
    s=open_recording(p)[0];r=recipe(s);r['signals'][0].update(start=0,end=1000,nfft=64,fmax=50);analyse(p,r,tmp_path/'out');report=validate(p,tmp_path/'out');assert report['status']=='passed',report
    with h5py.File(tmp_path/'out/numeric.h5') as f:
        assert f['signal0/extreme_count'][:].tolist()==[0,1,3];assert f['signal0/longest_extreme_run'][2]==3;assert f['signal0/valid_count'][2]==1000
