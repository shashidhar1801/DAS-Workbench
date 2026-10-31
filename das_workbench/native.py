"""Versioned native public profiles; the legacy OOI adapter is unchanged.

Readers return native dtype in canonical time × channel order. Portable metadata
never contains the local path. Interpretation is deliberately separate from bytes.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import struct
import numpy as np
import h5py
from scipy.io import loadmat

CONTRACT = 'public-native-2'
LAKE_PUBLICATION_SHA256 = 'ccc1e437ab35912018c3de1a1fed5ef56789095dce515bd4658a45ea940a110d'
LAKE_PUBLICATION_BYTES = 355911896
TILE_BYTES = 64 * 1024**2
# Documentary interpretation is attached to exact reviewed recordings, never schema alone.
DOCUMENTED_SOURCES = {
    'medsea': (38324400, 'a430a81533be0a88da65981ecf5f0dea67d74d49fec14f32f3ce090c95607e04'),
    'svalbard': (3201638976, 'e8e91c57232a4126c8857e06fd30e25b05557ea84423e2d52bda22aaabdbedf7'),
    'trondheim': (238744024, '6908a9d9d0576de02a386449a17502fbc290f5eb29ac7ed6c28116bcaf382655'),
}


def documented_source(path, source):
    candidates = [key for key, (size, digest) in DOCUMENTED_SOURCES.items() if size == source['bytes']]
    if candidates and source['sha256'] is None:
        source['sha256'] = sha(path)
        source['integrity'] = 'Full SHA256 computed for documentary interpretation'
    return next((key for key in candidates if source['sha256'] == DOCUMENTED_SOURCES[key][1]), None)


def mat_unit_interpretation(units):
    # Two explicitly supported declarations: ordered [data,time,space] tokens,
    # or the publisher's labelled equivalents. No substring-based acceptance.
    values = [str(x).strip().casefold() for x in np.asarray(units).reshape(-1)]
    supported = [['nanostrain', 'second', 'meter'],
                 ['data: nanostrain (1e-9)', 'time: second', 'space: meter']]
    return values in supported



def text(x):
    if isinstance(x, (bytes, np.bytes_)): return x.decode('utf8')
    return str(x)


def plain(x):
    if isinstance(x, np.ndarray): return [plain(v) for v in x.tolist()] if x.ndim else plain(x.item())
    if isinstance(x, np.generic): return plain(x.item())
    if isinstance(x, bytes): return x.decode('utf8', errors='replace')
    if isinstance(x, dict): return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [plain(v) for v in x]
    return x


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(4*1024**2), b''): h.update(b)
    return h.hexdigest()


def signature(path):
    s = Path(path).stat()
    return dict(bytes=s.st_size, mtime_ns=s.st_mtime_ns)


def identity(path):
    path = Path(path).resolve(); sig = signature(path)
    for root in path.parents:
        acq = root/'_acquisition'
        if (acq/'manifest.resolved.json').exists():
            rel = path.relative_to(root).as_posix()
            entries = json.loads((acq/'manifest.resolved.json').read_text())['files']
            for e in entries:
                if e['relative_path'] == rel:
                    v = json.loads((acq/'verification'/f"{e['id']}.json").read_text())
                    same = sig == dict(bytes=v['actual_output_bytes'], mtime_ns=v.get('file_mtime_ns'))
                    digest = v['local_sha256'] if same else sha(path)
                    if digest != v['local_sha256']: raise ValueError('Source differs from verified acquisition')
                    return dict(relative_path=rel, sha256=digest, record=e['record_id'], **sig,
                                integrity='Saved acquisition audit; size/mtime match' if same else 'Changed signature; SHA256 reverified',
                                doi='10.5281/zenodo.'+e['record_id'])
    return dict(relative_path=path.name, sha256=None, record=None, **sig,
                integrity='Signature only; full hash supplied at analysis', doi=None)


def mat_numeric_payload(path, variable='data'):
    """Parse uncompressed MAT v5 tags without reading the waveform payload."""
    size = Path(path).stat().st_size
    with open(path, 'rb') as f:
        header = f.read(128)
        if len(header) != 128 or header[126:128] not in (b'IM', b'MI'):
            raise ValueError('Not a supported MAT v5 header')
        endian = '<' if header[126:128] == b'IM' else '>'
        if struct.unpack(endian+'H', header[124:126])[0] != 256: raise ValueError('Unsupported MAT version')
        def tag():
            raw = f.read(8)
            if len(raw) != 8: raise ValueError('Truncated MAT tag')
            a, b = struct.unpack(endian+'II', raw)
            if a >> 16:
                typ, n = struct.unpack(endian+'HH', raw[:4])
                if n > 4: raise ValueError('Invalid small MAT tag')
                return typ, n, f.tell()-4, f.tell(), raw[4:4+n]
            pos = f.tell(); end = pos + ((b+7)//8)*8
            if pos+b > size: raise ValueError('MAT tag exceeds file')
            return a, b, pos, end, None
        while f.tell() < size:
            typ, n, pos, outer_end, small = tag()
            if typ == 15: raise ValueError('Compressed MAT requires explicit bounded conversion; not supported by this profile')
            if typ != 14: f.seek(outer_end); continue
            fields = []
            for _ in range(3):
                t, z, p, e, s = tag(); value = s if s is not None else f.read(z)
                fields.append((t, value)); f.seek(e)
            flags = struct.unpack(endian+'II', fields[0][1])
            dims = struct.unpack(endian+'i'*(len(fields[1][1])//4), fields[1][1])
            name = fields[2][1].decode('utf8')
            if name == variable:
                if flags[0] & 0x800 or (flags[0] & 255) != 6: raise ValueError('Expected real mxDOUBLE matrix')
                t, z, p, e, s = tag()
                if t != 9 or len(dims) != 2 or min(dims) <= 0 or z != int(np.prod(dims))*8 or p+z > pos+n:
                    raise ValueError('Invalid real double payload dimensions/type/extent')
                return dict(offset=p, shape=dims, dtype=endian+'f8', order='F', payload_bytes=z)
            f.seek(outer_end)
    raise ValueError('MAT variable not found: '+variable)


@dataclass
class Signal:
    path: Path
    group: str
    profile: str
    shape: tuple
    dtype: str
    axes: tuple
    dt: float
    origin: str
    clock: str
    channels: dict
    coordinates: dict
    quantity: str
    unit: str
    calibration: str
    metadata: dict
    source: dict
    payload: dict | None = None
    time_vector: np.ndarray | None = None
    quality: str = 'Finite samples only; native quality flags absent, not proof of good quality'
    max_read_bytes: int = 0

    @property
    def n(self): return self.shape[self.axes.index('time')]
    @property
    def c(self): return self.shape[self.axes.index('channel')] if 'channel' in self.axes else 1
    @property
    def fs(self): return 1/self.dt
    @property
    def pressure(self):
        return (self.metadata.get('received_pressure_eligible') is True
                and self.quantity == 'Received sound pressure' and self.unit == 'Pa')
    def check(self):
        if signature(self.path) != {k:self.source[k] for k in ('bytes','mtime_ns')}: raise ValueError('Source changed during use')
    def times(self, a, b):
        return self.time_vector[a:b].copy() if self.time_vector is not None else np.arange(a,b,dtype=np.float64)*self.dt
    def summary(self, vectors=False):
        def vec(x):
            x=np.asarray(x)
            return plain(x) if vectors else dict(count=len(x),first=plain(x[0]),last=plain(x[-1]))
        return dict(contract=CONTRACT,profile=self.profile,source=self.source,group=self.group,native_shape=self.shape,
                    native_dtype=self.dtype,native_axes=self.axes,n=self.n,channels=self.c,dt=self.dt,fs=self.fs,
                    duration=self.n*self.dt,origin=self.origin,clock=self.clock,
                    channel_ids={k:vec(v) for k,v in self.channels.items()},coordinates={k:vec(v) for k,v in self.coordinates.items()},
                    quantity=self.quantity,unit=self.unit,conversion='identity; no additional scaling',calibration=self.calibration,
                    metadata=self.metadata,quality=self.quality,received_pressure=self.pressure,
                    native_time='explicit x2_time_s' if self.time_vector is not None else 'origin + integer sample index × native dt')
    def read(self, a, b, channels):
        ch=np.asarray(channels,dtype=np.int64)
        if not (0<=a<b<=self.n) or not len(ch) or np.any(ch<0) or np.any(ch>=self.c) or np.any(np.diff(ch)<=0):
            raise ValueError('Invalid half-open samples or sorted unique channel indices')
        logical=(b-a)*len(ch)*np.dtype(self.dtype).itemsize
        if logical>TILE_BYTES: raise ValueError('Native tile exceeds 64 MiB; split the request')
        self.check()
        if self.payload:
            p=self.payload
            m=np.memmap(self.path,mode='r',dtype=p['dtype'],offset=p['offset'],shape=tuple(p['shape']),order='F')
            out=np.array(m[ch,a:b].T,copy=True); del m
        else:
            with h5py.File(self.path,'r') as f:
                d=f[self.group]
                if d.ndim==1: out=d[a:b][:,None]
                elif self.axes==('time','channel'): out=d[a:b,ch]
                else: out=d[ch,a:b].T
        self.max_read_bytes=max(self.max_read_bytes,out.nbytes)
        return out


def open_recording(path):
    path=Path(path).resolve(); source=identity(path); documented=documented_source(path,source)
    if not h5py.is_hdf5(path):
        p=mat_numeric_payload(path)
        names=['x1_absolute_channel','x1_relative_channel','x1_position_m','x1_distance_from_shore_m','x2_time_s',
               'info_sample_interval_s','info_sampling_frequency_Hz','info_GL_m','info_SSI_m','info_timestamp','info_units']
        m=loadmat(path,variable_names=names,squeeze_me=True)
        if any(k not in m for k in names): raise ValueError('Incomplete native MAT metadata')
        c,n=p['shape'];t=np.asarray(m['x2_time_s'],dtype=np.float64);dt=float(m['info_sample_interval_s'])
        if t.shape!=(n,) or not np.all(np.isfinite(t)) or not np.allclose(t,np.arange(n)*dt,rtol=0,atol=64*np.finfo(float).eps*max(1,abs(t[-1]))):
            raise ValueError('Nonuniform/gapped MAT time vector; contiguous spectral analysis blocked')
        channels={k:np.asarray(m[v]) for k,v in [('relative ID','x1_relative_channel'),('absolute ID','x1_absolute_channel')]}
        coordinates={'Distance from shore (m)':np.asarray(m['x1_distance_from_shore_m']),
                     'Distance from interrogator (m)':np.asarray(m['x1_position_m'])}
        if any(v.shape!=(c,) for v in [*channels.values(),*coordinates.values()]): raise ValueError('MAT coordinate mapping mismatch')
        if not np.isclose(float(m['info_sampling_frequency_Hz']),1/dt,rtol=1e-12): raise ValueError('MAT rate mismatch')
        qualified=documented=='svalbard'; supported=mat_unit_interpretation(m['info_units'])
        origin=datetime.strptime(text(m['info_timestamp']),'%Y%m%d %H:%M:%S').isoformat()+('Z' if qualified else '')
        state='qualified by source documentation' if qualified and supported else 'declared' if supported else 'unresolved'
        return [Signal(path,'data','svalbard-mat5-1',tuple(p['shape']),p['dtype'],('channel','time'),dt,origin,
                  'UTC associated by repository; native timestamp has no timezone' if qualified else 'Recording clock; timezone and external synchronization not established',
                  channels,coordinates,'Longitudinal strain, source declared' if supported else 'Unresolved native quantity',
                  'nanostrain' if supported else 'native unit',
                  'Source-declared nanostrain; no pressure calibration' if supported else 'Unknown unit convention; physical interpretation disabled',
                  dict(gauge_m=float(m['info_GL_m']),index_scale_m=float(m['info_SSI_m']),
                  units=plain(m['info_units']),interpretation_state=state,documented_source=documented,
                  clock_state='qualified by source documentation' if qualified else 'unresolved',
                  findings='Unit convention recognized; values retained without conversion' if supported else 'Unsupported info_units declaration; values retained without conversion',
                  coordinate_finding='Shore and interrogator origins differ; never substitute vectors'),source,p,t)]
    with h5py.File(path,'r') as f:
        if 'data' in f and 'header/dt' in f:
            d=f['data'];h=f['header'];names=[text(v) for v in h['dimensionNames'][:]]
            if sorted(names)!=['distance','time'] or d.ndim!=2: raise ValueError('Unsupported ASN axes')
            axes=tuple('channel' if x=='distance' else x for x in names)
            ids=h['channels'][:];c=d.shape[axes.index('channel')]
            if ids.shape!=(c,): raise ValueError('ASN channel mapping mismatch')
            if 'missingSamples' in h and h['missingSamples'].size: raise ValueError('Nonempty ASN missingSamples schema requires explicit mask mapping')
            dt=float(h['dt'][()]);unit=text(h['unit'][()]);name=text(h['name'][()])
            expected_units={'phase rate per distance':{'rad/(s*m)'},'strain rate':{'1/s','s^-1','(nm/m)/s'},'strain':{'strain','nanostrain','1'},'velocity':{'m/s'}}
            conflict=(documented=='trondheim' or (name.casefold() in expected_units and unit not in expected_units[name.casefold()]))
            coord=f['cableSpec/sensorDistances'][:] if 'cableSpec/sensorDistances' in f else ids*float(h['dx'][()])
            if coord.shape!=(c,) or not np.all(np.isfinite(coord)): raise ValueError('ASN coordinate mapping mismatch')
            metadata={k:plain(h[k][()]) for k in ['dt','dx','time','gaugeLength','name','unit','dataScale','sensitivities','sensitivityUnits']}
            metadata['distance_mapping']='native cableSpec/sensorDistances' if 'cableSpec/sensorDistances' in f else 'header/channels × header/dx; index-derived, not map distance'
            metadata['findings']=('Quantity metadata conflict; stored values retained without conversion' if conflict else
                                  'Gauge header versus README4m unresolved; spacing and analysis stride are separate' if documented=='medsea' else
                                  'Header declarations retained; source documentation and independent calibration not established')
            metadata.update(interpretation_state='conflicting' if conflict else 'qualified by source documentation' if documented=='medsea' else 'declared',
                            documented_source=documented,clock_state='qualified by source documentation' if documented=='medsea' else 'unresolved')
            timestamp=datetime.fromtimestamp(float(h['time'][()]),timezone.utc)
            origin=timestamp.isoformat() if documented else timestamp.replace(tzinfo=None).isoformat()
            return [Signal(path,'/data','asn-hdf5-1',d.shape,str(d.dtype),axes,dt,origin,
                     'Unix epoch; UTC supported by source README' if documented=='medsea' else 'Interrogator clock; no AIS correction' if documented=='trondheim' else 'Recording clock; external synchronization and timezone not established',
                     {'native ID':ids},{'Distance along fibre (m)':coord},
                     'Native signal amplitude; quantity metadata conflict' if conflict else 'Phase rate per distance, as stored' if documented=='medsea' else name,
                     'native unit' if conflict else unit,'Unresolved quantity calibration' if conflict else 'Header declared; not independently calibrated',metadata,source,
                     quality='No missing samples declared; finite samples retained; no independent quality assurance' if 'missingSamples' in h else 'Quality flags absent; finite samples retained')]
        # Exact source identity limits the author-code scaling/calibration evidence
        # to the reviewed publication. Similar group names do not inherit it.
        if source['sha256'] is None and source['bytes']==LAKE_PUBLICATION_BYTES:
            source['sha256']=sha(path);source['integrity']='Full SHA256 computed for publication provenance decision'
        published=source['sha256']==LAKE_PUBLICATION_SHA256
        groups=[]
        for group in ['DAS','Recorder-C','Source']:
            if group not in f: continue
            d=f[group];a=dict(d.attrs);is_das=group=='DAS'
            needed=['OutputDataRate','PartStartTime','PartEndTime'] if is_das else ['Sample Rate (Hz)','Start Time','End Time']
            if any(k not in a for k in needed): raise ValueError('Incomplete Lake schema')
            fs=float(a[needed[0]]);origin=text(a[needed[1]]);end=text(a[needed[2]])
            n=d.shape[0];c=d.shape[1] if d.ndim==2 else 1
            span=(datetime.fromisoformat(end)-datetime.fromisoformat(origin)).total_seconds()
            if abs(span-n/fs)>1e-6: raise ValueError('Lake endpoint/rate/count mismatch')
            declared_unit=text(a.get('RawDataUnit' if is_das else 'Unit','')).strip()
            declared_quantity=text(a.get('Signal','')).strip()
            valid_das=(is_das and declared_unit=='(nm/m)/s' and d.dtype==np.dtype('int16')
                       and float(a.get('AmpScaling',np.nan))==3.5400390625
                       and float(a.get('GaugeLength',np.nan))==10. and fs==5000.)
            expected='Sound Pressure' if group=='Recorder-C' else 'Sound pressure @1m'
            valid_pressure=(not is_das and declared_unit=='Pa' and declared_quantity==expected)
            interpreted=published and (valid_das if is_das else valid_pressure)
            meta=plain(a);meta.update(declared_quantity=declared_quantity,declared_unit=declared_unit,
                publication_provenance_verified=published,quantity_metadata_valid=valid_das if is_das else valid_pressure,
                received_pressure_eligible=bool(interpreted and group=='Recorder-C'))
            if interpreted:
                meta['findings']=('Publisher code scales before int16 storage; identity interpretation; extrema require QC. Distance endpoints do not supply a coordinate vector.' if is_das else 'Publisher calibration already applied; analogue response not established')
                meta['interpretation_state']='qualified by source documentation'
                meta['interpretation_evidence']=('Reviewed publication SHA256 + author Compile_Publication_Data code: scaling before int16 storage' if is_das else 'Reviewed publication SHA256 + report p15 + exact native Signal/Unit attributes')
                quantity='Published strain-rate integers (scaling already applied)' if is_das else ('Received sound pressure' if group=='Recorder-C' else 'Nominal source pressure at 1 m')
                unit='(nm/m)/s' if is_das else 'Pa'
                calibration='Publisher scaling; quantized int16; QC dependent' if is_das else 'Publisher calibration; not independently calibrated'
            else:
                meta['findings']='Unqualified quantity/scaling provenance or conflicting/missing declaration. Native values retained without conversion. Received-pressure levels disabled.'
                meta['interpretation_state']='unresolved'
                meta['interpretation_evidence']='No compatible verified publication provenance; group name is insufficient'
                quantity='Unqualified native amplitude';unit=declared_unit or 'native unit';calibration='Unknown; no verified conversion or calibration provenance'
            groups.append(Signal(path,'/'+group,'lake-hdf5-2',d.shape,str(d.dtype),('time','channel') if d.ndim==2 else ('time',),1/fs,origin,'Explicit timezone-aware timestamp attributes' if datetime.fromisoformat(origin.replace('Z','+00:00')).utcoffset() is not None else 'Recording clock; timezone and external synchronization not established',
                 {'channel index':np.arange(c)}, {'Channel index':np.arange(c)},quantity,unit,calibration,meta,source))
        if not groups: raise ValueError('This recording format is not supported in this workspace. Use phase analysis for compatible OptaSense recordings.')
        return groups
