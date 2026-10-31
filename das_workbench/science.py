"""Numerical primitives. No UI, file-layout assumptions, or display transforms."""
from functools import lru_cache
import importlib.metadata
import platform
import numpy as np
from scipy import fft


def hann(n):
    return .5 - .5*np.cos(2*np.pi*np.arange(n, dtype=np.float64)/n)


def prepare(samples, detrend):
    if detrend == "constant":
        # Preserve weak variations on both signs of int32 offsets.
        if np.issubdtype(samples.dtype, np.integer):
            x = (samples.astype(np.int64)-samples[..., :1].astype(np.int64)).astype(np.float64)
        else:
            x = samples.astype(np.float64)-samples[..., :1].astype(np.float64)
        x -= np.mean(x, axis=-1, keepdims=True, dtype=np.float64)
    elif detrend == "none":
        x = samples.astype(np.float64)
    else:
        raise ValueError("Unsupported detrending.")
    return x


def normalize_fft(z, fs, w, nfft):
    # Upcast complex64 components before squaring and summing.
    p = (z.real.astype(np.float64)**2 + z.imag.astype(np.float64)**2) / (fs*np.sum(w*w))
    p[..., 1:-1 if nfft % 2 == 0 else None] *= 2
    return p


def spectral_errors(p, ref, df):
    total = np.sum(ref, axis=-1)*df
    residual = np.sum(np.abs(p-ref), axis=-1)*df
    good = total > 0
    relative = float(np.max(residual[good]/total[good])) if np.any(good) else 0.0
    zero_error = float(np.max(residual[~good])) if np.any(~good) else 0.0
    peak = np.max(ref, axis=-1, keepdims=True)
    bins = (ref > 0) & (ref >= peak*1e-6)
    db = float(np.max(np.abs(10*np.log10(p[bins]/ref[bins])))) if np.any(bins) else 0.0
    return dict(relative_integrated_error=relative, max_supported_bin_db_error=db,
                zero_residual_power=zero_error)


def periodograms(samples, fs, nfft, detrend="constant", backend="cpu", bands=()):
    x = prepare(samples, detrend)
    n = x.shape[-1]
    w = hann(n)
    weighted = x*w
    reference = normalize_fft(fft.rfft(weighted, n=nfft, axis=-1, workers=1), fs, w, nfft)
    report = dict(requested=backend, executed="cpu", preprocessing="CPU int64-safe offset then float64 centering",
                  fft_precision="float64/complex128", reduction_precision="float64", fallback=None)
    if detrend == "none":
        report["preprocessing"] = "CPU float64; original DC preserved"
    if backend == "auto":
        report["fallback"] = "Auto uses CPU until a device/workload has measured end-to-end benefit."
    elif backend in ("mlx", "cupy"):
        if detrend == "none":
            report["fallback"] = "Undetrended/DC-preserving spectra use CPU float64."
        else:
            try:
                if backend == "mlx":
                    import mlx.core as mx
                    a = mx.array(weighted.astype(np.float32))
                    z = mx.fft.rfft(a, n=nfft, axis=-1, stream=mx.gpu)
                    mx.eval(z)
                    z = np.array(z)
                    report["device"] = dict(name=mx.device_info().get("device_name"),backend_version=importlib.metadata.version("mlx"))
                else:
                    import cupy as cp
                    z = cp.asnumpy(cp.fft.rfft(cp.asarray(weighted.astype(np.float32)), n=nfft, axis=-1))
                    props = cp.cuda.runtime.getDeviceProperties(cp.cuda.Device().id)
                    name = props.get("name", "unknown")
                    if isinstance(name,bytes):name=name.decode()
                    report["device"] = dict(name=name,backend_version=cp.__version__,runtime=cp.cuda.runtime.runtimeGetVersion(),driver=cp.cuda.runtime.driverGetVersion())
                candidate = normalize_fft(z, fs, w, nfft)
                # Some batched GPU FFT sizes return tiny residuals for an exactly
                # zero weighted input. Its DFT is analytically zero; retain exact
                # zeros rather than introducing a numerical floor or false power.
                exact_zero = np.all(weighted == 0,axis=-1)
                report["analytic_zero_windows"] = int(np.count_nonzero(exact_zero))
                report["raw_zero_window_residual_power"] = float(np.max(np.sum(candidate[exact_zero],axis=-1)*fs/nfft)) if np.any(exact_zero) else 0.0
                candidate[exact_zero] = 0.
                errors = spectral_errors(candidate, reference, fs/nfft)
                # Internal release audits each GPU batch against CPU, rather than
                # extrapolating a startup check to all signals. Cost is disclosed.
                approved = errors["relative_integrated_error"] <= 1e-4 and errors["max_supported_bin_db_error"] <= .05 and errors["zero_residual_power"] <= 1e-12
                for b in bands:
                    s = slice(b["first_bin"],b["last_bin"]+1)
                    bp = np.sum(candidate[...,s],axis=-1)*(fs/nfft)
                    br = np.sum(reference[...,s],axis=-1)*(fs/nfft)
                    total_power = np.sum(reference,axis=-1)*(fs/nfft)
                    tol = 1e-4*br+1e-10*total_power
                    approved = approved and bool(np.all(np.abs(bp-br) <= np.where(total_power>0,tol,1e-12)))
                report["audit"] = errors
                report["cpu_audit_each_batch"] = True
                if approved:
                    reference = candidate
                    report.update(executed=backend, fft_precision="float32/complex64")
                else:
                    report["fallback"] = "GPU batch failed the declared PSD/band tolerances; CPU reference retained."
            except Exception as exc:
                report["fallback"] = f"GPU unavailable or failed; CPU reference retained: {type(exc).__name__}: {exc}"
                if backend == "mlx":
                    try:
                        import mlx.core as mx
                        mx.clear_cache()
                    except Exception:
                        pass
    energy = np.sum(x*x*w*w,axis=-1)/np.sum(w*w)
    return reference, energy, report


def describe(values):
    values = np.asarray(values, dtype=np.float64)
    finite = values[np.isfinite(values)]
    if not len(finite):
        return dict(valid_count=0, excluded_count=int(values.size), minimum=None, maximum=None,
                    q05=None, q25=None, median=None, q75=None, q95=None, iqr=None, equivalent_rms=None)
    q = np.quantile(finite, [.05,.25,.5,.75,.95], method="linear")
    return dict(valid_count=int(len(finite)), excluded_count=int(values.size-len(finite)),
                minimum=float(np.min(finite)), maximum=float(np.max(finite)),
                q05=float(q[0]), q25=float(q[1]), median=float(q[2]), q75=float(q[3]),
                q95=float(q[4]), iqr=float(q[3]-q[1]),
                equivalent_rms=float(np.sqrt(np.mean(finite*finite))),
                quantile_method="numpy linear", aggregation="time windows within this channel")


@lru_cache(maxsize=1)
def capabilities():
    result = {"cpu": {"available":True,"precision":"float64","status":"reference"},
              "auto": {"available":True,"policy":"CPU until measured workload calibration"}}
    for name in ("mlx", "cupy"):
        try:
            if name == "mlx":
                import mlx.core as mx
                z = mx.fft.rfft(mx.array(np.arange(32,dtype=np.float32)), stream=mx.gpu)
                mx.eval(z)
                detail = dict(mx.device_info())
            else:
                import cupy as cp
                z = cp.fft.rfft(cp.arange(32,dtype=cp.float32))
                cp.asnumpy(z)
                detail = dict(device=str(cp.cuda.runtime.getDeviceProperties(0)),
                              runtime=cp.cuda.runtime.runtimeGetVersion(), driver=cp.cuda.runtime.driverGetVersion())
            version = importlib.metadata.version("mlx") if name=="mlx" else cp.__version__
            result[name] = dict(available=True, version=version, detail=detail,
                                status="experimental; each requested FFT batch CPU-audited; RMS/statistics stay on CPU")
        except Exception as exc:
            result[name] = dict(available=False, reason=str(exc))
    return result
