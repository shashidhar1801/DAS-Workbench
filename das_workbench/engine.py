"""Bounded, restartable analyses shared by GUI and replay.

Scientifically complete window groups own their start indices. Unit files are
atomic, checksummed checkpoints. Consolidation has one HDF5 writer.
"""
from __future__ import annotations
from pathlib import Path
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.metadata
import math
import os
import platform
import shutil
import time
import uuid
import numpy as np
import h5py
from . import __version__, ALGORITHM_VERSION
from .contracts import (Selection, AnalysisRecipe, digest, jsonable, atomic_json,
                        read_json, file_hash, run_from_dict, recipe_from_dict, iso)
from .adapter import RunReader, assert_signature, validate_time_and_status
from .science import periodograms, describe


def versions():
    out = {"application":__version__, "algorithm":ALGORITHM_VERSION,
           "python":platform.python_version(), "platform":platform.platform()}
    for name in ("numpy", "scipy", "h5py", "matplotlib", "PySide6", "pyqtgraph", "mlx", "cupy", "cupy-cuda12x", "cupy-cuda13x"):
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return out


def candidate_count(n, window, hop):
    return max(0, (n-window)//hop+1)


def estimate(run, selection, recipe):
    s, r = selection.resolve(run), recipe.resolve(run.first)
    c = len(s["channels"])
    counts = dict(welch=candidate_count(s["count"],r["window"],r["welch_hop"]),
                  stft=candidate_count(s["count"],r["window"],r["stft_hop"]),
                  rms=candidate_count(s["count"],r["rms_bin"],r["rms_bin"]))
    if counts["welch"] < 2:
        raise ValueError(f"Welch needs at least two complete {r['actual_window_s']:g} s windows at the requested hop. Enlarge the selection.")
    nf = r["nfft"]//2+1
    final = c*counts["stft"]*nf*8 if recipe.spectrogram else 0
    final += c*counts["stft"]*len(r["bands"])*16 + c*counts["rms"]*8 + c*nf*8
    final += sum(counts.values())*(32+2*c)
    # Unit products include per-window masks/coordinates, Welch partials and metadata.
    units = final + math.ceil(counts["welch"]/32)*c*nf*8
    metadata_allowance = max(8*1024**2, sum(counts.values())*c//32*4096)
    return dict(resolved_selection=s, resolved_recipe=r, candidates=counts,
                final_products_uncompressed_bytes=final,
                retained_checkpoints_uncompressed_bytes=units,
                temporary_consolidation_bytes=final,
                metadata_allowance_bytes=metadata_allowance, cache_allowance_bytes=64*1024**2,
                conservative_required_free_bytes=2*final+units+metadata_allowance+64*1024**2,
                input_samples_per_channel=s["count"], channels=c,
                nominal_duration_s=s["count"]/r["fs"],
                coverage="Final valid coverage and exclusions are established by the completed job.")


def _selected_sources(run, sel):
    return [s for s in run.files if s.end_us > sel.start_us and s.first_us < sel.end_us]


def create_job(directory, run, roots, selection, recipe, source_doi=None):
    out = Path(directory)
    if out.exists() and (out/"job.json").exists():
        raise FileExistsError("This output already contains a job. Resume it or choose a new directory.")
    plan = estimate(run, selection, recipe)
    out.mkdir(parents=True, exist_ok=True)
    (out/"units").mkdir(exist_ok=True)
    identity = dict(schema_version=1, algorithm=ALGORITHM_VERSION,
                    run=jsonable(run), selection=jsonable(selection), recipe=jsonable(recipe),
                    resolved=plan, software=versions(), source_doi=source_doi)
    fingerprint = digest(identity)
    job = dict(id=uuid.uuid4().hex, fingerprint=fingerprint, identity=identity,
               state="queued", completed_units=0, created=datetime.now(timezone.utc).isoformat())
    atomic_json(out/"job.json", job)
    atomic_json(out/"local_roots.json", roots)
    return job


def unit_plan(run, selected, r, recipe):
    channels = selected["channels"]
    for ci in range(0,len(channels),4):
        ch = channels[ci:ci+4]
        for kind, n, hop in (("welch",r["window"],r["welch_hop"]),
                             ("stft",r["window"],r["stft_hop"]),
                             ("rms",r["rms_bin"],r["rms_bin"])):
            if kind == "stft" and not recipe.spectrogram and not r["bands"]:
                continue
            nw = candidate_count(selected["count"],n,hop)
            # Limit transform temporaries and the underlying raw span independently.
            bytes_per_window = len(ch)*(n*72+(r["nfft"]//2+1)*48)
            batch = max(1,min(32, 128*1024**2//max(1,bytes_per_window),
                              max(1,(8_000_000//len(ch)-n)//hop+1)))
            for wi in range(0,nw,batch):
                size = min(batch,nw-wi)
                key = f"c{ci:06d}_{kind}_w{wi:010d}"
                yield dict(key=key, channel_offset=ci, channels=ch, kind=kind,
                           window=n, hop=hop, window_offset=wi, count=size,
                           starts=selected["start_sample"]+(np.arange(wi,wi+size,dtype=np.int64)*hop))


class Stopped(Exception):
    def __init__(self, state):
        self.state = state


def _stop(control):
    command = control()
    if command in ("pause", "cancel"):
        raise Stopped("paused" if command == "pause" else "cancelled")


def _ac_means(reader, selected, channels, r, control):
    count = 0
    delta_mean = np.zeros(len(channels),dtype=np.float64)
    offset = None
    for a in range(selected["start_sample"], selected["stop_sample"], 32768):
        _stop(control)
        x, valid, _ = reader.read(channels,a,min(a+32768,selected["stop_sample"]))
        if not np.any(valid):
            continue
        v = x[:,valid]
        if offset is None:
            offset = v[:,:1].astype(np.int64)
        y = (v.astype(np.int64)-offset).astype(np.float64)
        n = v.shape[1]
        # Chan combination of bounded block means.
        delta_mean += (np.mean(y,axis=1)-delta_mean)*(n/(count+n))
        count += n
    if count == 0:
        raise ValueError("AC-RMS has no valid samples for its selection-wide reference mean.")
    return dict(means=(offset[:,0]+delta_mean).tolist(), valid_count=count,
                reference="all valid native samples in selected continuous interval; same mean for every RMS bin")


def _write_unit(path, unit, reader, r, recipe, ac, fingerprint):
    begin = time.perf_counter()
    starts, n = unit["starts"], unit["window"]
    x, mask, reasons = reader.read(unit["channels"],int(starts[0]),int(starts[-1])+n)
    read_seconds = time.perf_counter()-begin
    windows = np.lib.stride_tricks.sliding_window_view(x,n,axis=-1)[:,::unit["hop"],:][:,:unit["count"]]
    vm = np.lib.stride_tricks.sliding_window_view(mask,n)[::unit["hop"]][:unit["count"]].all(axis=-1)
    wr = np.bitwise_or.reduce(np.lib.stride_tricks.sliding_window_view(reasons,n)[::unit["hop"]][:unit["count"]],axis=-1)
    arrays = {"starts":starts,"valid":vm,"reasons":wr}
    kind = unit["kind"]
    report = dict(requested=recipe.backend,executed="cpu",reduction_precision="float64")
    if kind == "rms":
        values = windows.astype(np.float64)
        if recipe.ac_rms:
            values -= np.array(ac["means"])[:,None,None]
        values = np.sqrt(np.mean(values*values,axis=-1))*r["scale"]
        values[:,~vm] = np.nan
        arrays["rms"] = values
    else:
        p, energy, report = periodograms(windows,r["fs"],r["nfft"],recipe.detrend,recipe.backend,r["bands"])
        p *= r["scale"]**2
        energy *= r["scale"]**2
        if kind == "welch":
            arrays["power_sum"] = np.sum(p[:,vm,:],axis=1)
            arrays["weighted_energy_sum"] = np.sum(energy[:,vm],axis=1)
        else:
            p[:,~vm,:] = np.nan
            if recipe.spectrogram:
                arrays["psd"] = p
            arrays["band_power"] = np.stack([np.sum(p[...,b["first_bin"]:b["last_bin"]+1],axis=-1)*r["df"]
                                             for b in r["bands"]],axis=-1) if r["bands"] else np.empty((len(unit["channels"]),len(starts),0))
    compute_seconds = time.perf_counter()-begin-read_seconds
    temp = path.with_suffix(".tmp.h5")
    with h5py.File(temp,"w") as f:
        f.attrs["fingerprint"] = fingerprint
        f.attrs["unit"] = json.dumps(jsonable({k:v for k,v in unit.items() if k != "starts"}))
        for name, array in arrays.items():
            f.create_dataset(name,data=array,compression="gzip" if array.size else None)
        f.flush()
    os.replace(temp,path)
    record = dict(fingerprint=fingerprint, unit_key=unit["key"], bytes=path.stat().st_size,
                  sha256=file_hash(path), backend=report, read_seconds=read_seconds,
                  compute_seconds=compute_seconds, duration_seconds=time.perf_counter()-begin,
                  valid_windows=int(vm.sum()), candidate_windows=len(vm))
    atomic_json(path.with_suffix(".json"),record)
    return record


def _unit_valid(path, fingerprint):
    try:
        r = read_json(path.with_suffix(".json"))
        if r["fingerprint"] != fingerprint or path.stat().st_size != r["bytes"] or file_hash(path) != r["sha256"]:
            return None
        with h5py.File(path,"r") as f:
            if f.attrs["fingerprint"] != fingerprint:
                return None
        return r
    except (OSError,KeyError,ValueError):
        return None


def run_job(directory, roots=None, progress=lambda *a:None, control=lambda:None, stop_after_units=None):
    out = Path(directory)
    job = read_json(out/"job.json")
    identity = job["identity"]
    if digest(identity) != job["fingerprint"] or identity["algorithm"] != ALGORITHM_VERSION:
        raise ValueError("Job recipe or algorithm identity changed; create a new job.")
    current_versions = versions()
    for package in ("application","algorithm","numpy","scipy","h5py"):
        if current_versions.get(package) != identity["software"].get(package):
            raise ValueError(f"{package} version changed since this job began. Replay into a new job instead of mixing checkpoints.")
    run = run_from_dict(identity["run"])
    selection = Selection(**{**identity["selection"], "channels":tuple(identity["selection"]["channels"])})
    recipe = recipe_from_dict(identity["recipe"])
    selected, r = selection.resolve(run), recipe.resolve(run.first)
    roots = roots or read_json(out/"local_roots.json")
    reader = RunReader(run,roots,recipe.strict_quality)
    plan_total = sum(1 for _ in unit_plan(run,selected,r,recipe))
    done = fresh = 0
    job.update(state="running",error=None,total_units=plan_total)
    atomic_json(out/"job.json",job)
    start = time.perf_counter()
    try:
        for src in _selected_sources(run,selection):
            assert_signature(src,roots)
        quality = dict(verified_status_samples=0,policy_valid_status_samples=0,reason_sample_counts={str(b):0 for b in (1,2,4,8,16,32)},warnings=[])
        for src in _selected_sources(run,selection):
            _stop(control)
            reasons,report = validate_time_and_status(src,roots)
            a = max(0,(selection.start_us-src.first_us+src.tick_us-1)//src.tick_us)
            b = min(src.samples,(selection.end_us-src.first_us+src.tick_us-1)//src.tick_us)
            selected_reasons = reasons[a:b]
            quality["verified_status_samples"] += b-a
            quality["policy_valid_status_samples"] += int(np.count_nonzero((selected_reasons & (63 if recipe.strict_quality else 3)) == 0))
            for bit in (1,2,4,8,16,32):
                quality["reason_sample_counts"][str(bit)] += int(np.count_nonzero(selected_reasons & bit))
            if report["warnings"]:quality["warnings"].append(dict(source_id=src.id,warnings=report["warnings"]))
        atomic_json(out/"selection_quality.json",quality)
        required = identity["resolved"]["conservative_required_free_bytes"]
        existing = sum(p.stat().st_size for p in (out/"units").glob("*.h5"))
        if shutil.disk_usage(out).free < max(64*1024**2,required-existing):
            raise OSError("Insufficient output disk space for products, checkpoints and consolidation. Choose a smaller selection or another local output disk.")
        ac_cache = {}
        for unit in unit_plan(run,selected,r,recipe):
            _stop(control)
            path = out/"units"/(unit["key"]+".h5")
            record = _unit_valid(path,job["fingerprint"])
            if record is None:
                ac = None
                if unit["kind"] == "rms" and recipe.ac_rms:
                    k = str(unit["channel_offset"])
                    if k not in ac_cache:
                        ap = out/"units"/("ac_"+k+".json")
                        saved = read_json(ap) if ap.exists() else {}
                        saved_hash = saved.pop("content_hash",None)
                        if saved.get("fingerprint") == job["fingerprint"] and saved_hash == digest(saved):
                            ac_cache[k] = saved
                        else:
                            ac_cache[k] = {**_ac_means(reader,selected,unit["channels"],r,control),"fingerprint":job["fingerprint"]}
                            atomic_json(ap,{**ac_cache[k],"content_hash":digest(ac_cache[k])})
                    ac = ac_cache[k]
                record = _write_unit(path,unit,reader,r,recipe,ac,job["fingerprint"])
                fresh += 1
            done += 1
            job.update(completed_units=done,elapsed_this_invocation_s=time.perf_counter()-start)
            atomic_json(out/"job.json",job)
            progress(done,plan_total,unit["key"])
            if stop_after_units is not None and fresh >= stop_after_units:
                raise Stopped("paused")
        _stop(control)
        consolidate(out,run,selected,r,recipe,job,control,progress)
        job.update(state="completed",completed_units=done,result="result.h5",elapsed_this_invocation_s=time.perf_counter()-start)
    except Stopped as exc:
        job.update(state=exc.state,completed_units=done)
    except Exception as exc:
        job.update(state="failed",error=f"{type(exc).__name__}: {exc}",completed_units=done)
        atomic_json(out/"job.json",job)
        raise
    finally:
        # Region history from all invocations survives resume. This is partial decode
        # evidence, never a full source checksum claim.
        vp = out/"decoded_regions.json"
        previous = read_json(vp) if vp.exists() else {}
        for sid, regions in reader.regions.items():
            dest = previous.setdefault(sid,{})
            for ch,bounds in regions.items():
                old = dest.get(ch,bounds)
                dest[ch] = [min(old[0],bounds[0]),max(old[1],bounds[1])]
        atomic_json(vp,previous)
        reader.close()
    atomic_json(out/"job.json",job)
    return job


def _dataset(f,name,shape,dtype="f8",unit=None,dimensions=None):
    chunks = tuple(max(1,min(s, 1 if i==0 else (32 if i==1 else 512))) for i,s in enumerate(shape))
    ds = f.create_dataset(name,shape=shape,dtype=dtype,chunks=chunks if all(shape) else None,
                          compression="gzip" if all(shape) else None,fillvalue=np.nan if dtype=="f8" else 0)
    if unit:
        ds.attrs["units"] = unit
    if dimensions:
        ds.attrs["dimensions"] = json.dumps(dimensions)
    return ds


def consolidate(out, run, selected, r, recipe, job, control, progress):
    import json
    temp = out/"result.tmp.h5"
    nc, nf = len(selected["channels"]),r["nfft"]//2+1
    counts = job["identity"]["resolved"]["candidates"]
    unit_name = r["amplitude_unit"]
    summaries = []
    backend_records = {}
    timing = dict(read_seconds=0.,compute_seconds=0.,unit_seconds=[])
    with h5py.File(temp,"w") as f:
        f.attrs["schema_version"] = 1
        f.attrs["fingerprint"] = job["fingerprint"]
        f.create_dataset("channels",data=selected["channels"])
        f.create_dataset("frequency_hz",data=np.arange(nf)*r["df"])
        if run.first.spacing_m is not None and run.first.start_locus is not None:
            f.create_dataset("distance_m",data=(np.array(selected["channels"])+run.first.start_locus)*run.first.spacing_m)
        _dataset(f,"welch/psd",(nc,nf),unit=unit_name+"^2/Hz",dimensions=["channel","frequency"])
        _dataset(f,"welch/weighted_energy",(nc,),unit=unit_name+"^2")
        _dataset(f,"welch/valid_count",(nc,),dtype="i8")
        for kind,n,hop in (("welch",r["window"],r["welch_hop"]),("stft",r["window"],r["stft_hop"]),("rms",r["rms_bin"],r["rms_bin"])):
            nw = counts[kind]
            _dataset(f,kind+"/valid",(nc,nw),dtype="?")
            _dataset(f,kind+"/reasons",(nc,nw),dtype="u2")
            for name in ("start_sample","first_us","last_us","end_exclusive_us","center_us_x2"):
                coordinate_unit = "zero-based logical-run sample index" if name=="start_sample" else ("twice Unix epoch microseconds UTC" if name=="center_us_x2" else "Unix epoch microseconds UTC")
                _dataset(f,kind+"/"+name,(nw,),dtype="i8",unit=coordinate_unit,dimensions=["window"])
            f[kind].attrs["window_samples"] = n
            f[kind].attrs["hop_samples"] = hop
            for a in range(0,nw,8192):
                b = min(nw,a+8192)
                starts = selected["start_sample"]+np.arange(a,b,dtype=np.int64)*hop
                first = run.start_us+starts*run.first.tick_us
                last = first+(n-1)*run.first.tick_us
                for name,values in (("start_sample",starts),("first_us",first),("last_us",last),
                                    ("end_exclusive_us",last+run.first.tick_us),("center_us_x2",first+last)):
                    f[kind+"/"+name][a:b] = values
        if recipe.spectrogram:
            _dataset(f,"stft/psd",(nc,counts["stft"],nf),unit=unit_name+"^2/Hz",dimensions=["channel","window","frequency"])
        _dataset(f,"stft/band_power",(nc,counts["stft"],len(r["bands"])),unit=unit_name+"^2")
        _dataset(f,"stft/band_rms",(nc,counts["stft"],len(r["bands"])),unit=unit_name)
        _dataset(f,"rms/value",(nc,counts["rms"]),unit=unit_name,dimensions=["channel","window"])
        # Channel blocks bound spectral aggregation memory even for an entire cable.
        current_ci = None
        total = valid_n = energy = None
        def commit_welch():
            if current_ci is None:
                return
            sl = slice(current_ci,current_ci+len(total))
            arr = total/valid_n if valid_n else np.full_like(total,np.nan)
            if valid_n < 2:
                arr[:] = np.nan
            f["welch/psd"][sl] = arr
            f["welch/valid_count"][sl] = valid_n
            f["welch/weighted_energy"][sl] = energy/valid_n if valid_n else np.nan
        for ui,unit in enumerate(unit_plan(run,selected,r,recipe)):
            _stop(control)
            ci = unit["channel_offset"]
            if ci != current_ci:
                commit_welch()
                current_ci = ci
                total = np.zeros((len(unit["channels"]),nf))
                energy = np.zeros(len(unit["channels"]))
                valid_n = 0
            record = read_json(out/"units"/(unit["key"]+".json"))
            br = record["backend"]
            key = digest(br)
            # Reports can differ numerically each batch; aggregate bounded maxima.
            signature = (br["executed"],str(br.get("fallback")))
            k = "|".join(signature)
            dest = backend_records.setdefault(k,dict(executed=br["executed"],fallback=br.get("fallback"),batches=0,
                                                     cpu_audit_each_batch=br.get("cpu_audit_each_batch",False),device=br.get("device"),audit_max={}))
            dest["batches"] += 1
            for name,value in br.get("audit",{}).items():
                dest["audit_max"][name] = max(dest["audit_max"].get(name,0),value)
            dest["analytic_zero_windows"] = dest.get("analytic_zero_windows",0)+br.get("analytic_zero_windows",0)
            dest["raw_zero_window_residual_power_max"] = max(dest.get("raw_zero_window_residual_power_max",0),br.get("raw_zero_window_residual_power",0))
            timing["read_seconds"] += record["read_seconds"]
            timing["compute_seconds"] += record["compute_seconds"]
            timing["unit_seconds"].append(record["duration_seconds"])
            kind, wi, size = unit["kind"],unit["window_offset"],unit["count"]
            cs, ws = slice(ci,ci+len(unit["channels"])),slice(wi,wi+size)
            with h5py.File(out/"units"/(unit["key"]+".h5"),"r") as u:
                vm = u["valid"][:]
                f[kind+"/valid"][cs,ws] = np.broadcast_to(vm,(len(unit["channels"]),size))
                f[kind+"/reasons"][cs,ws] = np.broadcast_to(u["reasons"][:],(len(unit["channels"]),size))
                if kind == "welch":
                    total += u["power_sum"][:]
                    energy += u["weighted_energy_sum"][:]
                    valid_n += int(vm.sum())
                elif kind == "stft":
                    if recipe.spectrogram:
                        f["stft/psd"][cs,ws,:] = u["psd"][:]
                    if r["bands"]:
                        bp = u["band_power"][:]
                        f["stft/band_power"][cs,ws,:] = bp
                        f["stft/band_rms"][cs,ws,:] = np.sqrt(bp)
                else:
                    f["rms/value"][cs,ws] = u["rms"][:]
            if ui % 16 == 0:
                progress(ui+1,job["total_units"],"Consolidating checked unit outputs")
        commit_welch()
        for ci,ch in enumerate(selected["channels"]):
            _stop(control)
            entry = dict(channel=ch,metrics={},coverage={})
            # Exact quantiles use the stored complete-window metrics, one channel at a time.
            if max(counts.values())*8 > 512*1024**2:
                raise ValueError("Exact per-channel statistics exceed the 512 MiB metric limit. Increase metric hop/bin or shorten the interval.")
            entry["metrics"]["ac_rms" if recipe.ac_rms else "rms"] = describe(f["rms/value"][ci,:])
            for bi,b in enumerate(r["bands"]):
                entry["metrics"][f"spectral_band_rms_{bi}"] = describe(f["stft/band_rms"][ci,:,bi])
            for kind,n,hop in (("welch",r["window"],r["welch_hop"]),("stft",r["window"],r["stft_hop"]),("rms",r["rms_bin"],r["rms_bin"])):
                valid = f[kind+"/valid"][ci,:]
                ix = np.flatnonzero(valid)
                covered = 0
                prev_end = 0
                for i in ix:
                    a,b = int(i)*hop,int(i)*hop+n
                    covered += max(0,b-max(a,prev_end))
                    prev_end = b
                reason = f[kind+"/reasons"][ci,:]
                entry["coverage"][kind] = dict(candidate_windows=len(valid),valid_windows=int(valid.sum()),
                    excluded_windows=int((~valid).sum()),valid_window_union_samples=covered,
                    valid_window_union_seconds=covered/r["fs"],
                    dropped_right_edge_samples=selected["count"]-((len(valid)-1)*hop+n) if len(valid) else selected["count"],
                    windows_with_reason={str(bit):int(np.count_nonzero(reason & bit)) for bit in (1,2,4,8,16,32)},
                    reason_counts_are_nonexclusive=True)
            summaries.append(entry)
        f.flush()
    os.replace(temp,out/"result.h5")
    atomic_json(out/"statistics.json",summaries)
    atomic_json(out/"recipe.json",dict(requested=jsonable(recipe),resolved=r,selection=selected))
    unit_durations = timing.pop("unit_seconds")
    timing.update(unit_count=len(unit_durations),unit_median_s=float(np.median(unit_durations)),unit_p95_s=float(np.quantile(unit_durations,.95)),
                  cache_condition="OS cache uncontrolled; no cold-cache claim")
    atomic_json(out/"timings.json",timing)
    manifest = dict(schema_version=1,status="completed",software=versions(),fingerprint=job["fingerprint"],
                    numeric_file="result.h5",numeric_sha256=file_hash(out/"result.h5"),
                    source_doi=job["identity"].get("source_doi"),
                    source_files=[jsonable(s) for s in run.files if s.end_us>selected["first_us"] and s.first_us<selected["end_exclusive_us"]],
                    source_verification="size/mtime signatures; analysed regions time/status validated and decoded; source payloads not fully hashed",
                    acquisition_id=run.first.acquisition_id,configuration_id=run.first.config_id,
                    selection=selected,recipe=r,backends=list(backend_records.values()),
                    quality=read_json(out/"selection_quality.json"),
                    precision="CPU int64-safe offset/float64 preparation and reductions; optional CPU-audited float32 GPU FFT",
                    normalization="one-sided |FFT(periodic Hann × detrended samples)|² / (fs × sum(window²)); interior bins doubled",
                    window_center="midpoint of first and last sample; exact integer center_us_x2 stores twice UTC microseconds",
                    spectral_aggregation="Welch arithmetic mean of valid linear periodograms; STFT has no time averaging",
                    rms_definition="selection-wide valid-sample mean removed before nonoverlapping bins" if recipe.ac_rms else "DC-preserving nonoverlapping waveform RMS",
                    calibration="counts and metadata-scaled phase only; no pressure/strain calibration",
                    reason_bits={1:"compromised sample",2:"compromised file",4:"modified data",8:"modified time",16:"GPS mode/sync absent",32:"unknown/missing status"},
                    quality_policy="exclude all reason bits" if recipe.strict_quality else "exclude reason bits 1 and 2; retain recorded warnings for others",
                    timing=timing,created=datetime.now(timezone.utc).isoformat())
    atomic_json(out/"result_manifest.json",manifest)


def waveform_preview(run, roots, selection, quantity="phase", bins=1600, control=lambda:None):
    """Full native samples feed min/max envelopes; overview values never feed science."""
    s = selection.resolve(run)
    if len(selection.channels) > 32:
        raise ValueError("Preview up to 32 selected channels; full jobs can use more.")
    scale = 1 if quantity == "counts" else run.first.phase_scale
    if scale is None:
        raise ValueError("Metadata-scaled phase is unavailable.")
    reader = RunReader(run,roots)
    step = max(1,math.ceil(s["count"]/bins))
    centers, minimum, maximum, means = [],[],[],[]
    # Read contiguous groups of display bins to avoid a decompression call per pixel.
    for a in range(s["start_sample"],s["stop_sample"],step*64):
        _stop(control)
        b = min(s["stop_sample"],a+step*64)
        x,valid,_ = reader.read(selection.channels,a,b)
        x = x.astype(np.float64)*scale
        x[:,~valid] = np.nan
        for offset in range(0,x.shape[1],step):
            values = x[:,offset:offset+step]
            centers.append((a+offset-s["start_sample"]+(values.shape[1]-1)/2)/float(run.first.rate))
            with np.errstate(invalid="ignore"):
                minimum.append(np.nanmin(values,axis=1));maximum.append(np.nanmax(values,axis=1));means.append(np.nanmean(values,axis=1))
    reader.close()
    return dict(time_s=np.array(centers),minimum=np.array(minimum).T,maximum=np.array(maximum).T,
                overview=np.array(means).T,channels=np.array(selection.channels),selection=s,
                display_reduction=f"min/max waveform envelope and mean image in {step}-sample bins; no channel interpolation",
                unit="count" if quantity == "counts" else "rad")


# Imported late to keep the module's public scientific dependency direction simple.
import json
