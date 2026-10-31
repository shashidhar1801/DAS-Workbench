from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path
import hashlib
import json
import os
import uuid

SCHEMA = 1
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def jsonable(value):
    if hasattr(value, "__dataclass_fields__"):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf8", errors="replace")
    if hasattr(value, "tolist"):
        return jsonable(value.tolist())
    if isinstance(value, Path):
        return str(value)
    return value


def digest(value):
    return hashlib.sha256(json.dumps(jsonable(value), sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with tmp.open("w", encoding="utf8") as f:
        json.dump(jsonable(value), f, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path):
    with Path(path).open(encoding="utf8") as f:
        return json.load(f)


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def utc_us(text):
    d = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("Enter UTC with Z or an explicit timezone offset.")
    dt = d.astimezone(timezone.utc) - EPOCH
    return dt.days * 86400_000000 + dt.seconds * 1_000000 + dt.microseconds


def iso(us):
    return (EPOCH + timedelta(microseconds=int(us))).isoformat(timespec="microseconds").replace("+00:00", "Z")


def samples(seconds, rate):
    x = Decimal(str(seconds))
    if not x.is_finite() or x <= 0:
        raise ValueError("Durations must be finite and positive.")
    return int((x * Decimal(str(rate))).to_integral_value(rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class SourceFile:
    id: str
    root_id: str
    relative_path: str
    size: int
    mtime_ns: int
    acquisition_id: str
    config_id: str
    rate: str
    tick_us: int
    channels: int
    samples: int
    first_us: int
    last_us: int
    start_index: int | None
    first_counter: int | None
    last_counter: int | None
    counter_step: int | None
    spacing_m: float | None
    start_locus: int | None
    gauge_m: float | None
    phase_scale: float | None
    axes: list
    metadata: dict
    validation: str = "metadata indexed"
    schema_version: int = SCHEMA
    adapter: str = "optasense-v1"
    dataset: str = "/Acquisition/Raw[0]/RawData"
    dtype: str = "int32"
    chunks: list | None = None
    compression: str | None = None

    def __post_init__(self):
        if self.schema_version != SCHEMA:
            raise ValueError("Unsupported source schema.")
        if self.channels<1 or self.samples<1 or self.tick_us<1 or Fraction(self.rate)*self.tick_us != 1_000_000:
            raise ValueError("Invalid source dimensions or exact sampling description.")
        if self.last_us != self.first_us+(self.samples-1)*self.tick_us:
            raise ValueError("Source endpoint span is not a uniform sample grid.")
        if Path(self.relative_path).is_absolute() or ".." in Path(self.relative_path).parts:
            raise ValueError("Source file references must remain relative to their declared root.")

    @property
    def end_us(self):
        return self.last_us + self.tick_us


@dataclass(frozen=True)
class ContinuousRun:
    id: str
    files: tuple[SourceFile, ...]
    boundary_before: str = "start of acquisition"

    @property
    def first(self):
        return self.files[0]

    @property
    def n(self):
        return sum(f.samples for f in self.files)

    @property
    def start_us(self):
        return self.first.first_us

    @property
    def end_us(self):
        return self.files[-1].end_us


@dataclass(frozen=True)
class Selection:
    start_us: int
    end_us: int
    channels: tuple[int, ...]
    schema_version: int = SCHEMA

    def resolve(self, run):
        if self.schema_version != SCHEMA:
            raise ValueError("Unsupported selection schema.")
        if type(self.start_us) is not int or type(self.end_us) is not int:
            raise ValueError("Selection endpoints must be integer UTC microseconds.")
        if not self.start_us < self.end_us:
            raise ValueError("The end of the UTC interval must follow the start.")
        if self.start_us < run.start_us or self.end_us > run.end_us:
            raise ValueError("Choose an interval within this continuous run; gaps cannot be bridged.")
        if not self.channels or len(set(self.channels)) != len(self.channels):
            raise ValueError("Select unique, zero-based channels.")
        if any(type(c) is not int or c < 0 or c >= run.first.channels for c in self.channels):
            raise ValueError(f"Channels must be integers from 0 to {run.first.channels - 1}.")
        dt = run.first.tick_us
        a = (self.start_us - run.start_us + dt - 1) // dt
        b = (self.end_us - run.start_us + dt - 1) // dt
        if b <= a:
            raise ValueError("No samples fall within this half-open interval.")
        return dict(start_sample=a, stop_sample=b, count=b-a,
                    first_us=run.start_us+a*dt, last_us=run.start_us+(b-1)*dt,
                    end_exclusive_us=run.start_us+b*dt, channels=list(self.channels),
                    requested_start_us=self.start_us, requested_end_us=self.end_us)


@dataclass(frozen=True)
class AnalysisRecipe:
    quantity: str = "phase"
    spectral_window_s: str = "4"
    welch_hop_s: str = "2"
    stft_hop_s: str = "1"
    nfft: int | None = None
    detrend: str = "constant"
    rms_bin_s: str = "1"
    ac_rms: bool = False
    bands: tuple = ((1.0, 20.0),)
    spectrogram: bool = True
    backend: str = "cpu"
    strict_quality: bool = False
    schema_version: int = SCHEMA

    def resolve(self, source):
        import numpy as np
        if self.schema_version != SCHEMA:
            raise ValueError("Unsupported recipe schema; migration is required.")
        if self.quantity not in ("counts", "phase"):
            raise ValueError("Only counts and metadata-scaled phase are supported. Pressure calibration is required for SPL.")
        if self.quantity == "phase" and source.phase_scale is None:
            raise ValueError("This source has no recognized phase scale. Choose stored counts.")
        if self.detrend not in ("constant", "none"):
            raise ValueError("Detrend must be constant or none.")
        if self.backend not in ("cpu", "auto", "mlx", "cupy"):
            raise ValueError("Unknown numerical backend.")
        fs = float(Fraction(source.rate))
        n = samples(self.spectral_window_s, source.rate)
        hw = samples(self.welch_hop_s, source.rate)
        hs = samples(self.stft_hop_s, source.rate)
        nr = samples(self.rms_bin_s, source.rate)
        nf = n if self.nfft is None else self.nfft
        if n < 2 or nr < 1 or not 1 <= hw <= n or not 1 <= hs <= n:
            raise ValueError("Window must contain ≥2 samples; hops must be 1…window; RMS bins must contain ≥1 sample.")
        if type(nf) is not int or nf < n:
            raise ValueError("FFT length must be an integer at least as large as the window.")
        # Prevent a single user-specified transform bypassing the bounded engine.
        if max(nf, n, nr) > 4_000_000:
            raise ValueError("A single window exceeds the limit of 4 million samples. Choose a shorter window.")
        f = np.arange(nf//2+1, dtype=np.float64) * (fs/nf)
        bands = []
        for edges in self.bands:
            if len(edges) != 2:
                raise ValueError("Bands require lower and upper frequency edges.")
            lo, hi = map(float, edges)
            if not np.isfinite([lo,hi]).all() or not 0 <= lo < hi <= fs/2:
                raise ValueError(f"Band {lo}–{hi} Hz must lie within 0–{fs/2:g} Hz.")
            ix = np.flatnonzero((f >= lo) & ((f <= hi) if hi == fs/2 else (f < hi)))
            if not len(ix):
                raise ValueError(f"Band {lo}–{hi} Hz contains no FFT bins at {fs/nf:g} Hz spacing.")
            bands.append(dict(requested_edges_hz=[lo,hi], first_bin=int(ix[0]), last_bin=int(ix[-1]),
                              first_center_hz=float(f[ix[0]]), last_center_hz=float(f[ix[-1]]),
                              integration="sum of included one-sided PSD bins × df; lower inclusive; upper exclusive except Nyquist"))
        w = .5 - .5*np.cos(2*np.pi*np.arange(n)/n)
        return dict(schema_version=SCHEMA, fs=fs, tick_us=source.tick_us, window=n,
                    welch_hop=hw, stft_hop=hs, rms_bin=nr, nfft=nf, df=fs/nf,
                    actual_window_s=n/fs, actual_welch_hop_s=hw/fs, actual_stft_hop_s=hs/fs,
                    actual_rms_bin_s=nr/fs, enbw_hz=float(fs*np.sum(w*w)/np.sum(w)**2),
                    window_definition="periodic Hann: 0.5 - 0.5*cos(2*pi*k/N)", bands=bands,
                    scale=1.0 if self.quantity == "counts" else source.phase_scale,
                    amplitude_unit="count" if self.quantity == "counts" else "rad",
                    quantity="stored counts" if self.quantity == "counts" else "processed phase, metadata-scaled radians",
                    detrend=self.detrend, ac_rms=self.ac_rms, strict_quality=self.strict_quality,
                    requested=jsonable(self))


def run_from_dict(value):
    return ContinuousRun(value["id"], tuple(SourceFile(**f) for f in value["files"]),
                         value.get("boundary_before", "start of acquisition"))


def recipe_from_dict(value):
    v = dict(value)
    if "bands" in v:
        v["bands"] = tuple(tuple(b) for b in v["bands"])
    return AnalysisRecipe(**v)
