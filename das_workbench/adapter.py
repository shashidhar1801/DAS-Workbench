"""Read-only OptaSense adapter. All HDF5 handles have bounded lifetimes."""
from __future__ import annotations
from pathlib import Path
from fractions import Fraction
from bisect import bisect_right
import numpy as np
import h5py
from .contracts import SourceFile, ContinuousRun, digest, jsonable, utc_us

RAW = "/Acquisition/Raw[0]"
DATA = RAW + "/RawData"
TIME = RAW + "/RawDataTime"


class SourceChangedError(RuntimeError):
    pass


class TimingError(ValueError):
    pass


def probe(path):
    p = Path(path)
    if p.name.startswith("._") or not p.is_file() or not h5py.is_hdf5(p):
        return False
    with h5py.File(p, "r") as f:
        return DATA in f and TIME in f


def read_metadata(path, root_id, root):
    p = Path(path)
    before = p.stat()
    with h5py.File(p, "r") as f:
        if DATA not in f or TIME not in f:
            raise ValueError("Unsupported HDF5 schema: OptaSense RawData and RawDataTime are required.")
        d, t = f[DATA], f[TIME]
        axes = jsonable(d.attrs.get("Dimensions", []))
        if axes not in (["locus", "time"], ["time", "locus"]) or d.ndim != 2:
            raise ValueError(f"Ambiguous axes {axes!r}; expected an explicit locus/time declaration.")
        if d.dtype.kind != "i" or d.dtype.itemsize != 4 or t.dtype.kind != "i" or t.dtype.itemsize != 8:
            raise ValueError("This adapter requires signed int32 waveforms and int64 timestamp ticks.")
        nc, nt = d.shape[axes.index("locus")], d.shape[axes.index("time")]
        if nt < 1 or t.shape != (nt,):
            raise ValueError("Timestamp length does not match the waveform time dimension.")
        meta = {}
        def visit(name, obj):
            if obj.attrs:
                meta["/"+name] = jsonable(dict(obj.attrs))
        f.visititems(visit)
        a, r = meta.get("/Acquisition", {}), meta.get(RAW, {})
        c = meta.get("/Acquisition/Custom", {})
        if "OutputDataRate" not in r:
            raise ValueError("Missing stored OutputDataRate; pulse rate is not a substitute.")
        rate = str(r["OutputDataRate"])
        fs = Fraction(rate)
        if fs <= 0 or (Fraction(1_000_000)/fs).denominator != 1:
            raise ValueError("This adapter requires a positive rate with exact integer microsecond ticks.")
        dt = int(Fraction(1_000_000)/fs)
        first, last = int(t[0]), int(t[-1])
        if first + (nt-1)*dt != last:
            raise TimingError("Irregular timestamp span within file; a schema/timing inspection is required.")
        for obj, count in ((d, nc*nt), (t, nt)):
            if "Count" in obj.attrs and int(obj.attrs["Count"]) != count:
                raise ValueError("Count attribute disagrees with dataset dimensions.")
            for attr, expected in (("PartStartTime", first), ("PartEndTime", last)):
                if attr in obj.attrs and utc_us(jsonable(obj.attrs[attr])) != expected:
                    raise TimingError(f"{attr} disagrees with actual timestamp endpoint.")
        counters = f.get(RAW+"/Custom/SampleCount")
        ca = cb = None
        if counters is not None:
            if counters.shape != (nt,):
                raise ValueError("SampleCount length mismatch.")
            ca, cb = int(counters[0]), int(counters[-1])
        step = c.get("Decimation Factor")
        if step is not None and int(step) <= 0:
            raise ValueError("Invalid counter increment.")
        phase = 2*np.pi/65536 if r.get("RawDataUnit") == "rad * 2PI/2^16" else None
        config = dict(acquisition=a, raw={k:v for k,v in r.items() if k != "uuid"},
                      custom={k:v for k,v in c.items() if k not in ("Data Compromised",)},
                      channels=nc, rate=rate, axes=axes, dtype=str(d.dtype))
        aid = a.get("AcquisitionId", a.get("uuid", "unknown"))
        relative = str(p.relative_to(Path(root)))
        if aid == "unknown":
            # Missing acquisition identity cannot justify joining separate files.
            config["unverified_acquisition_source"] = [root_id,relative]
        stable = digest(dict(root_id=root_id, relative_path=relative, size=before.st_size,
                             first=first, last=last, config=digest(config)))
        result = SourceFile(id=stable, root_id=root_id, relative_path=relative, size=before.st_size,
                            mtime_ns=before.st_mtime_ns, acquisition_id=aid, config_id=digest(config),
                            rate=rate, tick_us=dt, channels=nc, samples=nt, first_us=first, last_us=last,
                            start_index=int(d.attrs["StartIndex"]) if "StartIndex" in d.attrs else None,
                            first_counter=ca, last_counter=cb, counter_step=int(step) if step is not None else None,
                            spacing_m=a.get("SpatialSamplingInterval"), start_locus=r.get("StartLocusIndex"),
                            gauge_m=a.get("GaugeLength"), phase_scale=phase, axes=axes, metadata=meta,
                            chunks=list(d.chunks) if d.chunks else None, compression=d.compression)
    if p.stat().st_mtime_ns != before.st_mtime_ns or p.stat().st_size != before.st_size:
        raise SourceChangedError("Source changed while reading metadata.")
    return result


def assert_signature(source, roots):
    p = Path(roots[source.root_id]) / source.relative_path
    s = p.stat()
    if s.st_size != source.size or s.st_mtime_ns != source.mtime_ns:
        raise SourceChangedError(f"Source signature changed: {source.relative_path}. Refresh and revalidate before replay.")
    return p


def validate_time_and_status(source, roots):
    p = assert_signature(source, roots)
    warnings = []
    with h5py.File(p, "r") as f:
        times = f[TIME][:]
        if times.dtype.kind != "i" or times.dtype.itemsize != 8:
            raise TimingError("Timestamps must remain int64.")
        if len(times) != source.samples or times[0] != source.first_us or times[-1] != source.last_us or np.any(np.diff(times) != source.tick_us):
            raise TimingError(f"Timestamp discontinuity in {source.relative_path}. The affected file cannot be analysed as a continuous run.")
        raw_status = {}
        for key in ("SampleCount", "GpBits", "GpsStatus", "PpsOffset"):
            ds = f.get(RAW+"/Custom/"+key)
            if ds is None:
                warnings.append(f"{key} metadata unknown")
                raw_status[key] = None
            else:
                expected = {"SampleCount":("i",8),"GpBits":("u",1),"GpsStatus":("u",1),"PpsOffset":("u",4)}[key]
                if (ds.dtype.kind,ds.dtype.itemsize) != expected:
                    raise ValueError(f"{key} dtype does not match the supported OptaSense schema.")
                if ds.shape != (source.samples,) or ("Count" in ds.attrs and int(ds.attrs["Count"]) != source.samples):
                    raise ValueError(f"{key} vector length/Count mismatch.")
                raw_status[key] = ds[:]
        counter = raw_status["SampleCount"]
        if counter is not None:
            if source.counter_step is None:
                warnings.append("Sample counter step unknown")
            elif np.any(np.diff(counter) != source.counter_step):
                raise TimingError(f"Sample counter discontinuity in {source.relative_path}.")
        # Reason bits are retained even when the policy does not exclude them.
        reasons = np.zeros(source.samples, dtype=np.uint16)
        if any(v is None for v in raw_status.values()) or source.counter_step is None:
            reasons |= 32
        gp, gps = raw_status["GpBits"], raw_status["GpsStatus"]
        if gp is None:
            reasons |= 32
        else:
            reasons[(gp & 2) != 0] |= 1
            reasons[(gp & 1) != 0] |= 4
            reasons[(gp & ~np.uint8(3)) != 0] |= 32
        compromised = source.metadata.get("/Acquisition/Custom", {}).get("Data Compromised")
        if compromised is None:
            reasons |= 32
        elif compromised:
            reasons |= 2
        if gps is None:
            reasons |= 32
        else:
            reasons[(gps & 4) != 0] |= 8
            reasons[(gps & 3) != 3] |= 16
            reasons[(gps & ~np.uint8(7)) != 0] |= 32
        names = {1:"compromised sample", 2:"file compromised", 4:"modified data",
                 8:"modified time", 16:"GPS mode/sync flag absent", 32:"unknown or missing status"}
        counts = {v:int(np.count_nonzero(reasons & k)) for k,v in names.items()}
        for k,v in names.items():
            if counts[v]:
                warnings.append(f"{v}: {counts[v]} samples")
    assert_signature(source, roots)
    return reasons, dict(state="time and status validated; waveform not fully verified",
                         reason_counts=counts, warnings=warnings,
                         status_values={k: None if v is None else jsonable(np.unique(v))
                                        for k,v in raw_status.items() if k in ("GpBits", "GpsStatus")})


def group_runs(sources):
    groups = {}
    for s in sources:
        groups.setdefault(s.config_id, []).append(s)
    runs, conflicts = [], []
    for config, files in sorted(groups.items()):
        files.sort(key=lambda s:(s.first_us, s.relative_path))
        current = []
        why = "start of acquisition"
        def finish():
            if current:
                runs.append(ContinuousRun(digest([x.id for x in current]), tuple(current), why))
        for s in files:
            reason = None
            if current:
                prev = current[-1]
                if s.first_us < prev.end_us:
                    reason = "overlap or duplicate coverage"
                    conflicts.append(dict(left=prev.id, right=s.id, reason=reason))
                elif s.first_us > prev.end_us:
                    reason = "timestamp gap"
                elif prev.last_counter is not None and s.first_counter is not None and prev.counter_step is not None and s.first_counter != prev.last_counter+prev.counter_step:
                    reason = "counter discontinuity"
                elif prev.start_index is not None and s.start_index is not None and s.start_index != prev.start_index+prev.samples:
                    reason = "stream-index discontinuity"
            if reason:
                finish()
                current = []
                why = reason
            current.append(s)
        finish()
    return sorted(runs, key=lambda r:r.start_us), conflicts


class RunReader:
    """Bounded hyperslab reads; validate vectors once per active source signature."""
    def __init__(self, run, roots, strict=False):
        self.run, self.roots, self.strict = run, roots, strict
        self.ends = np.cumsum([s.samples for s in run.files]).tolist()
        self.validated = {}
        self.validation_reports = {}
        self.regions = {}  # compact decoded bounding ranges, per source/channel

    def read(self, channels, start, stop):
        if not 0 <= start < stop <= self.run.n:
            raise IndexError("Read falls outside the continuous run.")
        if any(c < 0 or c >= self.run.first.channels for c in channels):
            raise IndexError("Channel outside source dimensions.")
        if (stop-start)*len(channels) > 32_000_000:
            raise ValueError("Read exceeds bounded reader limit; divide into smaller blocks.")
        out = np.empty((len(channels), stop-start), dtype=np.int32)
        reasons = np.empty(stop-start, dtype=np.uint16)
        # h5py fancy indices must increase; restore the caller's order afterwards.
        ordered = sorted(set(channels))
        inverse = [ordered.index(c) for c in channels]
        first = bisect_right(self.ends, start)
        for i in range(first, len(self.run.files)):
            base = self.ends[i-1] if i else 0
            if base >= stop:
                break
            s = self.run.files[i]
            a, b = max(start, base)-base, min(stop, self.ends[i])-base
            path = assert_signature(s, self.roots)
            if s.id not in self.validated:
                rs, report = validate_time_and_status(s, self.roots)
                # Only two full status vectors are retained at once.
                if len(self.validated) >= 2:
                    self.validated.pop(next(iter(self.validated)))
                self.validated[s.id] = rs
                self.validation_reports[s.id] = report
            sl = slice(base+a-start, base+b-start)
            reasons[sl] = self.validated[s.id][a:b]
            with h5py.File(path, "r", rdcc_nbytes=32*1024**2) as f:
                d = f[DATA]
                # Contiguous channels share compressed chunks; one hyperslab avoids
                # fancy-index overhead, while sparse selections remain bounded.
                cs = slice(ordered[0], ordered[-1]+1) if ordered == list(range(ordered[0], ordered[-1]+1)) else ordered
                block = d[cs, a:b] if s.axes[0] == "locus" else d[a:b, cs].T
                out[:, sl] = block[inverse]
            assert_signature(s, self.roots)
            regions = self.regions.setdefault(s.id, {})
            for ch in channels:
                previous = regions.get(str(ch), [a,b])
                regions[str(ch)] = [min(a,previous[0]), max(b,previous[1])]
        valid = (reasons & (63 if self.strict else 3)) == 0
        return out, valid, reasons

    def close(self):
        self.validated.clear()
