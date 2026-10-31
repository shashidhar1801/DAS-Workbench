from pathlib import Path
import csv
import math
import shutil
import numpy as np
import h5py
from .contracts import read_json,atomic_json,file_hash,iso


def result_view(directory, channel_index=0, max_time=1600, max_frequency=1800):
    directory = Path(directory)
    manifest = read_json(directory/"result_manifest.json")
    with h5py.File(directory/"result.h5","r") as f:
        if f.attrs["fingerprint"] != manifest["fingerprint"]:
            raise ValueError("Result identity does not match its manifest.")
        channel = int(f["channels"][channel_index])
        freq = f["frequency_hz"][:]
        psd = f["welch/psd"][channel_index]
        origin = manifest["selection"]["first_us"]
        times = (f["rms/center_us_x2"][:]-2*origin).astype(np.float64)/2e6
        rms = f["rms/value"][channel_index,:]
        spec = st = sf = None
        ts = fs = 1
        if "stft/psd" in f:
            _,nt,nf = f["stft/psd"].shape
            ts,fs = max(1,math.ceil(nt/max_time)),max(1,math.ceil(nf/max_frequency))
            spec = f["stft/psd"][channel_index,::ts,::fs].T
            st = (f["stft/center_us_x2"][::ts]-2*origin).astype(np.float64)/2e6
            sf = freq[::fs]
    return dict(manifest=manifest,channel=channel,frequency=freq,psd=psd,rms_time=times,rms=rms,
                stft=spec,stft_time=st,stft_frequency=sf,time_stride=ts,frequency_stride=fs,
                display_reduction="strided display columns/bins only; no numerical averaging; full arrays retained")


def channel_summary(directory):
    with h5py.File(Path(directory)/"result.h5","r") as f:
        ds = f["welch/psd"]
        nf = ds.shape[1]
        mean,low,high = np.empty(nf),np.empty(nf),np.empty(nf)
        for a in range(0,nf,64):
            b = min(nf,a+64)
            values = ds[:,a:b]
            mean[a:b] = np.nanmean(values,axis=0)
            low[a:b],high[a:b] = np.nanquantile(values,[.05,.95],axis=0,method="linear")
        return dict(frequency=f["frequency_hz"][:],mean=mean,q05=low,q95=high,
                    count=ds.shape[0],definition="arithmetic mean of linear channel PSDs; Q05–Q95 describes channels, not a confidence interval")


def display_db(power, floor_db=-180.):
    power = np.asarray(power,dtype=float)
    out = np.full_like(power,np.nan)
    good = np.isfinite(power) & (power >= 0)
    out[good] = 10*np.log10(np.maximum(power[good],10**(floor_db/10)))
    return out


def export_bundle(directory, destination, channel_index=0, settings=None):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    directory,destination = Path(directory),Path(destination)
    if destination.resolve() == directory.resolve():
        raise ValueError("Choose a separate export folder.")
    destination.mkdir(parents=True,exist_ok=True)
    settings = dict(dict(width_cm=20.,height_cm=22.,dpi=300,font_size=9.,colormap="viridis",
                         log_frequency=False,frequency_min=0.,frequency_max=None,
                         vmin_db=None,vmax_db=None,floor_db=-180.),**(settings or {}))
    if not 5 <= settings["width_cm"] <= 100 or not 5 <= settings["height_cm"] <= 100 or not 72 <= settings["dpi"] <= 1200:
        raise ValueError("Figure size must be 5–100 cm and DPI 72–1200.")
    # Prevent accidentally overwriting a different result bundle.
    if (destination/"result_manifest.json").exists():
        raise FileExistsError("Export folder already contains a result. Choose a new folder.")
    manifest = read_json(directory/"result_manifest.json")
    if file_hash(directory/"result.h5") != manifest["numeric_sha256"]:
        raise ValueError("Numeric artifact checksum failed; resume/rebuild the result before exporting.")
    needed = (directory/"result.h5").stat().st_size+64*1024**2
    if shutil.disk_usage(destination).free < needed:
        raise OSError("Not enough disk space to include the portable numeric result.")
    view = result_view(directory,channel_index)
    r = manifest["recipe"]
    amp = r["amplitude_unit"]
    freqmax = settings["frequency_max"] if settings["frequency_max"] is not None else r["fs"]/2
    freqmin = settings["frequency_min"]
    if settings["log_frequency"]:
        freqmin = max(freqmin,r["df"])
    if not 0 <= freqmin < freqmax <= r["fs"]/2:
        raise ValueError("Display frequency limits must be inside the recorded range.")
    with matplotlib.rc_context({"font.size":settings["font_size"],"svg.fonttype":"none","pdf.fonttype":42}):
        fig = Figure(figsize=(settings["width_cm"]/2.54,settings["height_cm"]/2.54),layout="constrained")
        FigureCanvasAgg(fig)
        axes = fig.subplots(3,1,gridspec_kw={"height_ratios":[1,1.5,.85]})
        axes[0].plot(view["frequency"],display_db(view["psd"],settings["floor_db"]),color="#176c80",lw=.9)
        axes[0].set(xlabel="Frequency (Hz)",ylabel=f"PSD (dB re 1 {amp}²/Hz)",xlim=(freqmin,freqmax),title="Welch power spectral density")
        axes[0].grid(alpha=.2)
        if settings["log_frequency"]: axes[0].set_xscale("log")
        if view["stft"] is not None:
            sf = view["stft_frequency"]
            keep = sf>0 if settings["log_frequency"] else np.ones(len(sf),dtype=bool)
            z = display_db(view["stft"][keep,:],settings["floor_db"])
            mesh = axes[1].pcolormesh(view["stft_time"],sf[keep],z,shading="nearest",cmap=settings["colormap"],
                                       vmin=settings["vmin_db"],vmax=settings["vmax_db"],rasterized=True)
            fig.colorbar(mesh,ax=axes[1],label=f"PSD (dB re 1 {amp}²/Hz)",pad=.02)
            axes[1].set(xlabel="Elapsed time from first selected sample (s)",ylabel="Frequency (Hz)",
                        ylim=(freqmin,freqmax),title="Spectrogram · individual windows, no time averaging")
            if settings["log_frequency"]: axes[1].set_yscale("log")
        else:
            axes[1].text(.5,.5,"Spectrogram storage was not requested",ha="center",transform=axes[1].transAxes)
        axes[2].plot(view["rms_time"],view["rms"],color="#176c80",lw=.9)
        axes[2].set(xlabel="Elapsed time from first selected sample (s)",ylabel=f"{'AC-' if r['ac_rms'] else ''}RMS ({amp})",
                    title=f"Nonoverlapping {r['actual_rms_bin_s']:g} s waveform RMS")
        axes[2].grid(alpha=.2)
        fig.suptitle(f"Channel {view['channel']} · {r['quantity']}\n{iso(manifest['selection']['first_us'])} · fs {r['fs']:g} Hz",fontsize=settings["font_size"]+1)
        for suffix in ("png","pdf","svg"):
            fig.savefig(destination/("figure."+suffix),dpi=settings["dpi"])
    for name in ("result.h5","result_manifest.json","recipe.json","statistics.json","decoded_regions.json","job.json"):
        if (directory/name).exists():
            shutil.copy2(directory/name,destination/name)
    atomic_json(destination/"root_map_template.json",{s["root_id"]:"REPLACE_WITH_LOCAL_SOURCE_ROOT" for s in manifest["source_files"]})
    settings.update(channel_index=channel_index,channel=view["channel"],time_stride=view["time_stride"],frequency_stride=view["frequency_stride"],
                    display_reduction=view["display_reduction"],reference=f"1 {amp}²/Hz",numeric_floor_applied=False)
    atomic_json(destination/"figure_settings.json",settings)
    caption = (f"Channel {view['channel']}, acquisition {manifest['acquisition_id']}. "
               f"UTC selection {iso(manifest['selection']['first_us'])} to {iso(manifest['selection']['end_exclusive_us'])} (end exclusive). "
               f"{r['quantity']}; periodic Hann {r['window']} samples ({r['actual_window_s']:g} s), "
               f"Welch hop {r['welch_hop']} samples, STFT hop {r['stft_hop']} samples, FFT length {r['nfft']}; "
               f"{r['detrend']} spectral detrending; one-sided density. Welch averages valid linear periodograms; "
               f"STFT preserves individual windows. RMS uses {r['rms_bin']} samples per nonoverlapping bin; "
               f"{manifest['rms_definition']}. Display uses every {view['time_stride']} STFT column and every "
               f"{view['frequency_stride']} frequency bin; no time averaging. Display floor {settings['floor_db']:g} dB, "
               f"colour limits {settings['vmin_db']}, {settings['vmax_db']} (None means automatic). "
               "All native numerical products, masks and exact coordinates are included in result.h5. "
               "Phase is metadata-scaled; pressure and strain calibration are unavailable.\n")
    (destination/"caption.txt").write_text(caption,encoding="utf8")
    # Bounded table: one row per channel/metric, not a spectrogram CSV dump.
    statistics = read_json(directory/"statistics.json")
    with (destination/"statistics.csv").open("w",newline="",encoding="utf8") as f:
        keys = ["channel","metric","valid_count","excluded_count","minimum","q05","q25","median","q75","q95","maximum","iqr","equivalent_rms"]
        writer = csv.DictWriter(f,fieldnames=keys,extrasaction="ignore");writer.writeheader()
        for row in statistics:
            for metric,values in row["metrics"].items():
                writer.writerow(dict(channel=row["channel"],metric=metric,**values))
    atomic_json(destination/"bundle_checksums.json",{p.name:file_hash(p) for p in destination.iterdir() if p.is_file() and p.name!="bundle_checksums.json"})
    return str(destination)
