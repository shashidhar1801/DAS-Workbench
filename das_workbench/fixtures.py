"""Synthetic data, explicitly identified and separate from OOI observations."""
from pathlib import Path
import numpy as np
import h5py
from .contracts import iso, atomic_json


def signal_fixture(rate=200, channels=32, seconds=12, seed=42):
    n = int(rate*seconds)
    t = np.arange(n)/rate
    rng = np.random.default_rng(seed)
    bases = [np.zeros(n),np.full(n,1_700_000_000),
             2000*np.sin(2*np.pi*10*t),2000*np.sin(2*np.pi*10.37*t),
             1000*np.sin(2*np.pi*(2*t+1.2*t*t)),rng.normal(0,500,n),
             1_700_000_000+3*np.sin(2*np.pi*7*t),
             -1_700_000_000+4*np.sin(2*np.pi*11*t),
             np.where(np.arange(n)==n//2,1_000_000,0),
             100*np.sin(2*np.pi*5*t)+900*np.sin(2*np.pi*20*t)]
    return np.stack([np.rint(bases[i%len(bases)]).astype(np.int32) for i in range(channels)])


def write_fixture(path, data, rate, first_us=1635984002025000, start_index=0,
                  counter_start=0, acquisition="synthetic-A", compromised=False,
                  gp=None, gps=None, transpose=False, axis_override=None, missing_status=False):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    channels, samples = data.shape
    tick = 1_000_000//rate
    times = first_us+np.arange(samples,dtype=np.int64)*tick
    with h5py.File(path,"w") as f:
        a = f.create_group("Acquisition")
        a.attrs.update(AcquisitionId=acquisition,SpatialSamplingInterval=2.1,GaugeLength=31.5,
                       NumberOfLoci=channels,StartLocusIndex=0,PulseRate=1000.,PulseWidth=150.,
                       VendorCode="SYNTHETIC OptaSense schema fixture",schemaVersion="2.0")
        a.create_group("Custom").attrs.update({"Data Compromised":int(compromised),"Decimation Factor":1,
                                                "Phase Fractional Width (Bits)":16,"Data Width (Bits)":32})
        r = a.create_group("Raw[0]")
        r.attrs.update(OutputDataRate=float(rate),RawDataUnit="rad * 2PI/2^16",StartLocusIndex=0,
                       RawDescription="Synthetic quantized phase for validation, not observational data")
        values = data.T if transpose else data
        chunks = (min(128,samples),min(510,channels)) if transpose else (min(510,channels),min(128,samples))
        d = r.create_dataset("RawData",data=values,chunks=chunks,compression="gzip",compression_opts=1)
        d.attrs.update(Dimensions=axis_override or (["time","locus"] if transpose else ["locus","time"]),
                       Count=data.size,StartIndex=start_index,PartStartTime=iso(times[0]),PartEndTime=iso(times[-1]))
        tm = r.create_dataset("RawDataTime",data=times)
        tm.attrs.update(Count=samples,PartStartTime=iso(times[0]),PartEndTime=iso(times[-1]))
        c = r.create_group("Custom")
        c.create_dataset("SampleCount",data=np.arange(samples,dtype=np.int64)+counter_start)
        if not missing_status:
            c.create_dataset("GpBits",data=np.zeros(samples,dtype=np.uint8) if gp is None else gp)
            c.create_dataset("GpsStatus",data=np.full(samples,3,dtype=np.uint8) if gps is None else gps)
            c.create_dataset("PpsOffset",data=np.zeros(samples,dtype=np.uint32))
    return path


def make_demo(directory):
    directory = Path(directory)
    data = signal_fixture(200,32,36)
    paths = []
    for i in range(3):
        paths.append(write_fixture(directory/f"synthetic_{i+1}.h5",data[:,i*2400:(i+1)*2400],200,
                                   first_us=1635984002025000+i*12_000_000,start_index=i*2400,counter_start=i*2400))
    atomic_json(directory/"SYNTHETIC.json",dict(description="Quantized deterministic signals for learning and validation; not OOI observations",rate=200,channels=32,seconds=36,seed=42))
    return paths
