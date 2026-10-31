"""Full-fibre time-domain overview. Display reduction never feeds RMS or trace.

All channel statistics use valid native samples and a selection-wide mean.
Integer-offset float64 Chan reductions avoid losing small signals on large DC.
"""
from pathlib import Path
import hashlib
import math
import os
import shutil
import tempfile
import time
import numpy as np
import h5py
from . import __version__
from .adapter import RunReader, assert_signature
from .contracts import (ContinuousRun, Selection, atomic_json, read_json, jsonable,
                        digest, file_hash, iso)

ALGORITHM = "full-fibre-overview-1"
DEFAULTS = dict(trace_channel=0, channel_stride=16, time_bin_samples=10,
                clip_percentile=95., colour_limit=None, strict_quality=False,
                title="DAS | full-fibre overview", rms_log=True)
STYLE = dict(width_cm=25., height_cm=18.5, dpi=300, font_size=9.)


def plan_overview(run, start_us, end_us, settings=None):
    """Metadata-only plan; never reads waveform payload or silently changes settings."""
    settings = dict(DEFAULTS, **(settings or {}))
    for key in ("trace_channel", "channel_stride", "time_bin_samples"):
        if type(settings[key]) is not int or settings[key] < (0 if key == "trace_channel" else 1):
            raise ValueError(f"{key} must be an integer in range.")
    source = run.first
    selection = Selection(start_us, end_us, (settings["trace_channel"],)).resolve(run)
    if source.spacing_m is None or not math.isfinite(source.spacing_m) or source.spacing_m <= 0 or source.start_locus is None:
        raise ValueError("Verified channel spacing and start locus are required for relative distance.")
    if source.phase_scale is None or not math.isfinite(source.phase_scale) or source.phase_scale <= 0:
        raise ValueError("Verified processed-phase scaling is required for this overview.")
    if not math.isfinite(settings["clip_percentile"]) or not 0 < settings["clip_percentile"] <= 100:
        raise ValueError("Colour percentile must be greater than 0 and at most 100.")
    limit = settings["colour_limit"]
    if limit is not None and (not math.isfinite(limit) or limit <= 0):
        raise ValueError("The symmetric colour limit must be finite and positive.")
    n = selection["count"]
    if settings["time_bin_samples"] > n:
        raise ValueError("Waterfall time bin cannot exceed the selection length.")
    nc = math.ceil(source.channels/settings["channel_stride"])
    nt = math.ceil(n/settings["time_bin_samples"])
    pixels = nc*nt
    max_columns = 8_000_000//nc
    suggested = max(settings["time_bin_samples"], math.ceil(n/max_columns)) if max_columns else None
    blockers = []
    if n > 10_000_000:
        blockers.append(f"Native trace {n:,} > 10,000,000 samples: shorten the UTC interval; waterfall coarsening cannot fix this limit.")
    if pixels > 8_000_000:
        blockers.append(f"Waterfall {nc:,} × {nt:,} = {pixels:,} pixels > 8,000,000. Explicitly accept bin {suggested} samples ({suggested/float(source.rate):g} s), retaining stride {settings['channel_stride']}, or shorten the interval.")
    block_n = max(settings["time_bin_samples"], 8192//settings["time_bin_samples"]*settings["time_bin_samples"])
    if block_n*min(510,source.channels) > 8_000_000:
        blockers.append("Requested bin exceeds the bounded read buffer; reduce bin or shorten the interval.")
    numeric = pixels*8+n*10+source.channels*40+1_000_000
    return settings, selection, dict(all_channels=source.channels, native_samples_per_channel=n,
        native_trace_samples=n, logical_read_bytes=source.channels*n*4,
        compressed_read_bytes=None, compressed_read_note="Unknown: chunk decompression, caching and storage determine physical I/O; logical bytes are not measured disk reads.",
        waterfall_rows=nc, waterfall_columns=nt, waterfall_pixels=pixels,
        bin_samples=settings["time_bin_samples"], bin_seconds=settings["time_bin_samples"]/float(source.rate),
        final_bin_samples=n-(nt-1)*settings["time_bin_samples"], final_bin_seconds=(n-(nt-1)*settings["time_bin_samples"])/float(source.rate),
        numeric_bytes_upper=numeric, temporary_disk_bytes_estimate=numeric,
        working_buffer_bytes_estimate=min(510,source.channels)*block_n*40+pixels*16+n*18+source.channels*64,
        memory_assumptions="Conservative numeric buffers: ≤510 channels, bin-aligned ≤8192 samples (or one larger bin), float64 temporaries, waterfall/percentile copies and trace/status; excludes HDF5/OS caches, Python, GUI and export renderer. Compressed output can differ.",
        allowed=not blockers, blocking_reasons=blockers, max_trace_samples=10_000_000, max_waterfall_pixels=8_000_000,
        suggested_bin_samples=suggested if pixels > 8_000_000 and n <= 10_000_000 and max_columns else None,
        computation="CPU float64; bounded channel/time reads; all native samples feed statistics")


def resolve_overview(run, start_us, end_us, settings=None):
    settings, selection, plan = plan_overview(run, start_us, end_us, settings)
    if not plan["allowed"]:
        raise ValueError(" ".join(plan["blocking_reasons"]))
    return settings, selection, plan


def _control(control):
    state = control()
    if state in ("cancel", "pause"):
        raise InterruptedError("Overview stopped; no partial result published. Generate again to restart.")


def compute_overview(destination, run, roots, start_us, end_us, settings=None,
                     source_doi=None, progress=lambda *args: None, control=lambda: None,
                     channel_block=510, time_block=8192, staging=None, planning_decision=None):
    # Restrict provenance/replay to the files actually intersecting the interval.
    run = ContinuousRun(run.id, tuple(s for s in run.files if s.first_us < end_us and s.end_us > start_us))
    if not run.files:
        raise ValueError("No source files intersect the requested interval.")
    settings, selected, estimate = resolve_overview(run, start_us, end_us, settings)
    if channel_block < 1 or channel_block > 1024 or time_block < 1:
        raise ValueError("Invalid bounded read dimensions.")
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("Choose a new overview result folder.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < estimate["numeric_bytes_upper"]+64*1024**2:
        raise OSError("Insufficient space for the overview result.")
    for source in run.files:
        assert_signature(source, roots)
    n, nc = selected["count"], run.first.channels
    selected["channels"] = list(range(nc))
    selected["channel_scope"] = "all recorded channels; trace channel specified separately in settings"
    stride, bin_n = settings["channel_stride"], settings["time_bin_samples"]
    rows = np.arange(0, nc, stride, dtype=np.int64)
    edges = np.minimum(np.arange(estimate["waterfall_columns"]+1, dtype=np.int64)*bin_n, n)
    # Align blocks to display bins; partial final bin has its true edge and count.
    block_n = max(bin_n, time_block//bin_n*bin_n)
    if block_n*channel_block > 8_000_000:
        raise ValueError("Time bin is too large for the bounded reader; reduce it.")
    if planning_decision:
        planning_decision = dict(planning_decision, accepted=dict(settings))
    identity = dict(algorithm=ALGORITHM, run=jsonable(run), start_us=start_us, end_us=end_us,
                    settings=settings, source_doi=source_doi)
    fingerprint = digest(identity)
    temp = Path(staging) if staging else Path(tempfile.mkdtemp(prefix=".overview-", dir=destination.parent))
    temp.mkdir(parents=True, exist_ok=True)
    reader = RunReader(run, roots, strict=settings["strict_quality"])
    begin = time.perf_counter()
    regions = []
    total = math.ceil(nc/channel_block)*math.ceil(n/block_n)
    done = 0
    try:
        with h5py.File(temp/"overview.h5", "w") as f:
            f.attrs.update(algorithm=ALGORITHM, fingerprint=fingerprint, unit="rad")
            distances = (np.arange(nc)+run.first.start_locus)*run.first.spacing_m
            f.create_dataset("channels", data=np.arange(nc))
            f.create_dataset("distance_m", data=distances).attrs["unit"] = "m"
            f.create_dataset("waterfall/channels", data=rows)
            f.create_dataset("waterfall/time_edges_s", data=edges/float(run.first.rate))
            wf = f.create_dataset("waterfall/phase_rad", (len(rows), len(edges)-1), dtype="f8", compression="gzip", shuffle=True)
            wf.attrs.update(unit="rad", definition="temporal bin means minus whole-selection channel mean; display only; bins touching excluded samples are NaN")
            mean_ds = f.create_dataset("channel_mean_rad", (nc,), dtype="f8")
            rms_ds = f.create_dataset("rms_rad", (nc,), dtype="f8")
            rms_ds.attrs.update(unit="rad", definition="sqrt(sum((x-mean(x))**2)/N_valid), population normalization; all valid native samples")
            counts_ds = f.create_dataset("valid_sample_count", (nc,), dtype="i8")
            trace = f.create_dataset("trace/phase_rad", (n,), dtype="f8", compression="gzip", shuffle=True)
            trace.attrs.update(channel=settings["trace_channel"], distance_m=distances[settings["trace_channel"]], unit="rad",
                               first_sample_utc_us=selected["first_us"],sample_interval_us=run.first.tick_us)
            quality = f.create_dataset("quality/reasons", (n,), dtype="u2", compression="gzip")
            quality.attrs["bit_meanings"] = "1 compromised sample; 2 file compromised; 4 modified data; 8 modified time; 16 GPS mode/sync flag absent; 32 unknown or missing status"
            quality.attrs["exclusion_bit_mask"] = 63 if settings["strict_quality"] else 3
            for ca in range(0, nc, channel_block):
                cb = min(nc, ca+channel_block)
                channels = list(range(ca, cb))
                ir = np.flatnonzero((rows >= ca) & (rows < cb))
                local = rows[ir]-ca
                offset = None
                count = 0
                mean, m2 = np.zeros(cb-ca), np.zeros(cb-ca)
                for a in range(0, n, block_n):
                    _control(control)
                    b = min(n, a+block_n)
                    raw, valid, reasons = reader.read(channels, selected["start_sample"]+a, selected["start_sample"]+b)
                    regions.append(dict(channels=[ca, cb], samples=[selected["start_sample"]+a, selected["start_sample"]+b],
                        sha256_int32_le_c_order=hashlib.sha256(raw.astype("<i4", copy=False).tobytes()).hexdigest()))
                    if offset is None:
                        offset = raw[:, 0].astype(np.float64)
                    x = raw.astype(np.float64)-offset[:, None]
                    k = int(valid.sum())
                    if k:
                        v = x[:, valid]
                        batch_mean = v.mean(axis=1)
                        v -= batch_mean[:, None]
                        batch_m2 = np.einsum("ij,ij->i", v, v)
                        delta = batch_mean-mean
                        m2 += batch_m2+delta*delta*(count*k/(count+k))
                        mean += delta*(k/(count+k))
                        count += k
                    x[:, ~valid] = np.nan
                    starts = np.arange(0, b-a, bin_n)
                    lengths = np.minimum(bin_n, b-a-starts)
                    if len(ir):
                        wf[ir[0]:ir[-1]+1, a//bin_n:math.ceil(b/bin_n)] = np.add.reduceat(x[local], starts, axis=1)/lengths
                    if ca <= settings["trace_channel"] < cb:
                        trace[a:b] = x[settings["trace_channel"]-ca]
                    if ca == 0:
                        quality[a:b] = reasons
                    done += 1
                    progress(done, total, f"Full-fibre overview · channels {ca}–{cb-1}")
                if count == 0:
                    raise ValueError("No valid samples in this interval under the selected quality policy.")
                scale = run.first.phase_scale
                mean_ds[ca:cb] = (offset+mean)*scale
                rms_ds[ca:cb] = np.sqrt(np.maximum(m2/count, 0))*scale
                counts_ds[ca:cb] = count
                if len(ir):
                    # Bounded by the explicit waterfall pixel cap.
                    wf[ir[0]:ir[-1]+1] = (wf[ir[0]:ir[-1]+1]-mean[local, None])*scale
                if ca <= settings["trace_channel"] < cb:
                    for a in range(0, n, block_n):
                        trace[a:a+block_n] = (trace[a:a+block_n]-mean[settings["trace_channel"]-ca])*scale
            reason_values, reason_counts = np.unique(quality[:], return_counts=True)
            coverage = dict(valid_samples=count, excluded_samples=n-count, total_samples=n,
                            reason_combinations={str(k):int(v) for k,v in zip(reason_values,reason_counts)})
            z = wf[:]
            finite = np.abs(z[np.isfinite(z)])
            # A valid record may have every display bin touch one invalid sample.
            auto = float(np.percentile(finite, settings["clip_percentile"])) if len(finite) else 0.
            limit = settings["colour_limit"] or auto
            fallback = limit == 0
            if fallback:
                limit = run.first.phase_scale
            colour = dict(limit_rad=limit, percentile_rad=auto, zero_limit_fallback=fallback,
                          clipped_fraction=float(np.mean(finite > limit)) if len(finite) else 0.,
                          finite_pixels=len(finite), missing_pixels=int(z.size-len(finite)))
        _control(control)
        for source in run.files:
            assert_signature(source, roots)
        manifest = dict(schema_version=1, software_version=__version__, algorithm=ALGORITHM,
            fingerprint=fingerprint, identity=identity, selection=selected, estimate=estimate,
            settings=settings, quality=coverage, colour=colour,
            display_planning=planning_decision or dict(action="requested settings accepted unchanged", requested=settings, accepted=settings),
            fs=float(run.first.rate), spacing_m=run.first.spacing_m, start_locus=run.first.start_locus,
            gauge_m=run.first.gauge_m, phase_scale=run.first.phase_scale,
            source_validation=reader.validation_reports, decoded_blocks=regions,
            elapsed_seconds=time.perf_counter()-begin, numeric_sha256=file_hash(temp/"overview.h5"),
            interpretation="Vendor-processed optical phase, not strain or pressure. Relative fibre coordinate, not map distance.")
        atomic_json(temp/"overview_manifest.json", manifest)
        atomic_json(temp/"overview_recipe.json", identity)
        atomic_json(temp/"local_roots.json", roots)
        (temp/"caption.txt").write_text(overview_caption(manifest), encoding="utf8")
        # Validate the closed artifact before the single atomic publication boundary.
        load_overview(temp)
        progress(total, total, "Ready to publish overview")
        _control(control)
        for source in run.files:
            assert_signature(source, roots)
        os.replace(temp, destination)
        return str(destination)
    finally:
        reader.close()
        if temp.exists():
            shutil.rmtree(temp)


def load_overview(directory):
    directory = Path(directory)
    m = read_json(directory/"overview_manifest.json")
    if file_hash(directory/"overview.h5") != m["numeric_sha256"]:
        raise ValueError("Overview checksum failed; regenerate from sources.")
    with h5py.File(directory/"overview.h5", "r") as f:
        if f.attrs["fingerprint"] != m["fingerprint"]:
            raise ValueError("Overview identity mismatch.")
        data = {key:f[key][:] for key in ("distance_m", "rms_rad", "channel_mean_rad", "valid_sample_count",
            "waterfall/channels", "waterfall/phase_rad", "waterfall/time_edges_s", "trace/phase_rad")}
    m["colour"] = saved_colour(m, data["waterfall/phase_rad"])
    return m, data


def saved_colour(m, waterfall):
    finite = np.abs(waterfall[np.isfinite(waterfall)])
    auto = float(np.percentile(finite, m["settings"]["clip_percentile"])) if len(finite) else 0.
    limit = m["settings"]["colour_limit"] or auto
    fallback = limit == 0
    if fallback: limit = m["phase_scale"]
    return dict(limit_rad=limit, percentile_rad=auto, zero_limit_fallback=fallback,
                clipped_fraction=float(np.mean(finite > limit)) if len(finite) else 0.,
                finite_pixels=len(finite), missing_pixels=int(waterfall.size-len(finite)))


def overview_caption(m):
    s, q, c = m["settings"], m["quality"], m["colour"]
    ch = s["trace_channel"]
    distance = (ch+m["start_locus"])*m["spacing_m"]/1000
    rule = f"{s['clip_percentile']:g}th percentile of absolute finite saved waterfall values" if s['colour_limit'] is None else "fixed user-specified symmetric limit"
    return (f"Vendor-processed optical phase. Full-fibre overview, {iso(m['selection']['first_us'])} to {iso(m['selection']['last_us'])} (last sample); "
        f"selection end exclusive {iso(m['selection']['end_exclusive_us'])}. Duration {m['selection']['count']/m['fs']:g} s; sampling {m['fs']:g} Hz. "
        f"A: selection-wide valid-sample channel mean removed. Spatial subsampling: every {s['channel_stride']}th channel; "
        f"temporal averaging: {s['time_bin_samples']}-sample arithmetic means ({s['time_bin_samples']/m['fs']:g} s), for display only. "
        f"Bins are anchored at the selection start across file/folder joins. Final partial bins retain their actual width. A bin touching any excluded sample is blank. "
        f"Colour rule: {rule}; ±{c['limit_rad']:.8g} rad, {100*c['clipped_fraction']:.5g}% of finite displayed values clipped (strictly beyond limits; ties not clipped). "
        f"B: zero-based channel {ch}, relative distance {distance:.6f} km, all valid native samples after selection-wide mean removal; invalid samples remain NaN. "
        f"C: per-channel temporal RMS = sqrt(sum((x-mean(x))²)/N_valid), population normalization, every channel over the full interval. "
        f"B and C have no additional filtering or time averaging. {q['valid_samples']:,}/{q['total_samples']:,} valid samples per channel; {q['excluded_samples']:,} excluded, never zero-filled. "
        f"Quality exclusion mask {63 if s['strict_quality'] else 3}: {'all status warnings' if s['strict_quality'] else 'compromised sample/file only; other flags retained'}. "
        f"Counts × {m['phase_scale']:.17g} rad/count. No strain or pressure calibration. "
        f"Distance = (zero-based channel + start locus {m['start_locus']}) × {m['spacing_m']:.17g} m; relative fibre coordinate, not map distance. "
        f"Zero RMS remains zero numerically, omitted on log axes; an all-zero result uses a disclosed linear axis. "
        f"CPU float64; algorithm {m['algorithm']}; numeric producer {m['software_version']}; presentation {__version__}. "
        f"Source DOI: {m['identity'].get('source_doi') or 'not supplied'}.\n")


def make_overview_figure(directory, style=None):
    import matplotlib
    from matplotlib.figure import Figure
    m, data = load_overview(directory)
    style = dict(STYLE, **(style or {}))
    if not (18 <= style["width_cm"] <= 40 and 14 <= style["height_cm"] <= 32 and
            72 <= style["dpi"] <= 600 and 7 <= style["font_size"] <= 14):
        raise ValueError("Overview figure: width 18–40 cm, height 14–32 cm, DPI 72–600, font 7–14 pt.")
    if style["font_size"] > min(style["width_cm"]*.4,style["height_cm"]*.5):
        raise ValueError("Increase the figure size or reduce its font: font size must be ≤ 0.4 × width in cm and ≤ 0.5 × height in cm to preserve the three-panel layout.")
    s = m["settings"]
    fs, count = m["fs"], m["selection"]["count"]
    duration = count/fs
    ch = s["trace_channel"]
    distance = data["distance_m"]/1000
    selected_distance = distance[ch]
    with matplotlib.rc_context({"font.size":style["font_size"], "font.family":"DejaVu Sans",
            "axes.spines.top":False, "axes.spines.right":False, "axes.labelcolor":"#24374b",
            "text.color":"#24374b", "xtick.color":"#40566d", "ytick.color":"#40566d",
            "path.simplify":False, "svg.fonttype":"none", "pdf.fonttype":42}):
        fig = Figure(figsize=(style["width_cm"]/2.54, style["height_cm"]/2.54), dpi=style["dpi"])
        # Explicit margins keep a common A/B/C layout in GUI, PNG, PDF and SVG.
        grid = fig.add_gridspec(2, 2, left=.085, right=.89, bottom=.185, top=.835,
                               height_ratios=[2.05,1], hspace=.48, wspace=.32)
        a, b, c = fig.add_subplot(grid[0,:]), fig.add_subplot(grid[1,0]), fig.add_subplot(grid[1,1])
        fig.text(.085,.957,s["title"],fontsize=style["font_size"]+4,weight="bold")
        fig.text(.085,.918,f"{iso(m['selection']['first_us'])}  ·  {duration:g} s  ·  UTC",fontsize=style["font_size"])
        gauge = f"{m['gauge_m']:.3f} m gauge length" if m["gauge_m"] is not None else "gauge length unavailable"
        fig.text(.085,.883,f"{fs:g} Hz  ·  {len(distance):,} channels  ·  {distance[-1]-distance[0]:.3f} km span  ·  {m['spacing_m']:.3f} m spacing  ·  {gauge}")
        centers = distance[data["waterfall/channels"]]
        # Sampled spatial cells cover fibre endpoints; no interpolation of data.
        ye = np.r_[distance[0]-m["spacing_m"]/2000, (centers[:-1]+centers[1:])/2,
                   distance[-1]+m["spacing_m"]/2000]
        cmap = matplotlib.colormaps["RdBu_r"].with_extremes(bad="#dfe4e8")
        limit = m["colour"]["limit_rad"]
        mesh = a.pcolormesh(data["waterfall/time_edges_s"], ye, data["waterfall/phase_rad"],
                           shading="flat",cmap=cmap,vmin=-limit,vmax=limit,rasterized=True)
        ca = fig.add_axes([.915, a.get_position().y0, .014, a.get_position().height])
        fig.colorbar(mesh,cax=ca,extend="both",label="Mean-removed phase (rad)")
        a.axhline(selected_distance,color="#26384b",lw=.65,ls="--")
        a.text(.985,selected_distance,f"ch {ch:,} · {selected_distance:.3f} km",ha="right",va="bottom",
               transform=a.get_yaxis_transform(),fontsize=style["font_size"]-1,
               bbox=dict(facecolor="white",edgecolor="none",alpha=.85,pad=1.5))
        a.set(title="A   Full-fibre distance–time overview · mean-removed phase",xlabel="Elapsed time (s)",
              ylabel="Distance along fibre (km)",xlim=(0,duration),ylim=(distance[0],max(distance[-1],distance[0]+m['spacing_m']/1000)))
        a.set_title(a.get_title(),loc="left",fontsize=style["font_size"]+1);a.set_title("")
        b.plot(np.arange(count)/fs,data["trace/phase_rad"],color="#008b9a",lw=.65)
        b.set(xlabel="Elapsed time (s)",ylabel="Mean-removed phase (rad)",xlim=(0,duration))
        b.set_title(f"B   Channel {ch:,} · {selected_distance:.3f} km",loc="left",fontsize=style["font_size"]+1)
        rms = data["rms_rad"]
        if s["rms_log"] and np.any(rms>0):
            c.set_yscale("log");plotted = np.where(rms>0,rms,np.nan)
            # Plain vector text is portable to SVG viewers without math tspan support.
            from matplotlib.ticker import FuncFormatter
            c.yaxis.set_major_formatter(FuncFormatter(lambda value, pos: f"{value:g}"))
        else:
            plotted=rms
        c.plot(distance,plotted,color="#36639b",lw=.6)
        c.set(xlabel="Distance along fibre (km)",ylabel="Phase RMS (rad)",xlim=(distance[0],max(distance[-1],distance[0]+m['spacing_m']/1000)))
        c.set_title(f"C   Per-channel phase RMS · full {duration:g} s",loc="left",fontsize=style["font_size"]+1)
        for ax in (b,c):ax.grid(alpha=.15)
        foot = [f"Vendor-processed optical phase. Waterfall: every {s['channel_stride']}th channel; {s['time_bin_samples']}-sample means ({s['time_bin_samples']/fs:g} s), display only.",
            f"Colour: ±{limit:.5g} rad; {100*m['colour']['clipped_fraction']:.2f}% clipped. Trace and RMS: {count:,} native samples/channel, {m['quality']['excluded_samples']:,} excluded.",
            "Selection-wide mean removal; no additional filtering of trace/RMS. No strain or pressure calibration.",
            "Relative fibre distance; zero-based channels. Exact UTC bounds, quality, recipe and provenance accompany this figure."]
        if s["rms_log"] and np.any(rms==0):
            foot.append(f"{np.count_nonzero(rms==0)} zero-RMS channels retained numerically; " + ("omitted on log axis." if np.any(rms>0) else "all-zero RMS: linear axis."))
        import textwrap
        width = int(style["width_cm"]*5.8*9/style["font_size"])
        def wrap_balanced(line):
            lines = textwrap.wrap(line, width=width)
            if len(lines)>1 and len(lines[-1].split())<4:
                words = lines[-2].split()
                while len(lines[-1].split())<4 and len(words)>4:
                    lines[-1] = words.pop()+" "+lines[-1]
                lines[-2] = " ".join(words)
            return "\n".join(lines)
        fig.text(.085,.025,"\n".join(map(wrap_balanced,foot)),fontsize=style["font_size"]-1,va="bottom",linespacing=1.35,color="#50647a")

    return fig


def export_overview(directory, destination, style=None):
    import matplotlib
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    directory, destination = Path(directory), Path(destination)
    if destination.exists():
        raise FileExistsError("Choose a new publication bundle folder.")
    style = dict(STYLE, **(style or {}))
    m, _ = load_overview(directory)
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".overview-export-",dir=destination.parent))
    try:
        fig = make_overview_figure(directory,style)
        FigureCanvasAgg(fig)
        with matplotlib.rc_context({"svg.fonttype":"none","pdf.fonttype":42,"path.simplify":False}):
            for suffix in ("png","pdf","svg"):
                fig.savefig(temp/f"overview.{suffix}",dpi=style["dpi"],facecolor="white")
        fig.clear()
        for name in ("overview.h5","overview_recipe.json"):
            shutil.copy2(directory/name,temp/name)
        from .overview_evidence import evidence_status
        evidence = evidence_status(directory, m)
        m["presentation_version"] = __version__
        m["derived_validation_status"] = evidence
        # Never carry a stale evidence pointer into a new portable export.
        m.pop("validation_report", None)
        if evidence["binding_valid"]:
            ref = read_json(directory/"overview_manifest.json")["validation_report"]
            shutil.copy2(directory/ref["path"], temp/"validation_report.json")
            m["validation_report"] = dict(path="validation_report.json", sha256=ref["sha256"])
        atomic_json(temp/"overview_manifest.json",m)
        (temp/"caption.txt").write_text(overview_caption(m)+"Derived-result validation: "+evidence["status"]+". "+evidence["explanation"]+"\n",encoding="utf8")
        atomic_json(temp/"figure_settings.json",style)
        atomic_json(temp/"root_map_template.json",{s["root_id"]:"RELINK_SOURCE_ROOT" for s in m["identity"]["run"]["files"]})
        with h5py.File(directory/"overview.h5","r") as f:
            np.savetxt(temp/"spatial_rms.csv",np.column_stack([f["channels"][:],f["distance_m"][:],f["rms_rad"][:],f["channel_mean_rad"][:],f["valid_sample_count"][:]]),
                       delimiter=",",header="channel_zero_based,distance_m,ac_rms_rad,mean_phase_rad,valid_sample_count",comments="",fmt=["%d","%.17g","%.17g","%.17g","%d"])
        atomic_json(temp/"bundle_checksums.json",{p.name:file_hash(p) for p in temp.iterdir() if p.is_file()})
        os.replace(temp,destination)
        return str(destination)
    finally:
        if temp.exists():shutil.rmtree(temp)
