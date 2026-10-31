import numpy as np
import pytest
from scipy.signal import welch, windows
from das_workbench.science import periodograms, prepare, describe, spectral_errors
from das_workbench.fixtures import signal_fixture


@pytest.mark.parametrize("rate",[125,200,1000])
@pytest.mark.parametrize("nfft_delta",[0,1,123])
@pytest.mark.parametrize("detrend",["constant","none"])
def test_cpu_psd_independent_scipy_and_parseval(rate,nfft_delta,detrend):
    x = signal_fixture(rate,10,12)
    n = rate*4
    nf = n+nfft_delta
    seg = np.lib.stride_tricks.sliding_window_view(x,n,axis=-1)[:,::n//2,:]
    p,e,_ = periodograms(seg,rate,nf,detrend)
    _,ref = welch(x.astype(np.float64),fs=rate,window=windows.hann(n,sym=False),nperseg=n,
                  noverlap=n//2,nfft=nf,detrend="constant" if detrend=="constant" else False,scaling="density")
    errors = spectral_errors(np.mean(p,axis=1),ref,rate/nf)
    assert errors["relative_integrated_error"] <= 1e-10
    assert errors["zero_residual_power"] <= 1e-12
    np.testing.assert_allclose(np.sum(p,axis=-1)*rate/nf,e,rtol=1e-10,atol=1e-12)
    if detrend == "constant":
        assert np.max(p[:2]) <= 1e-12


def test_direct_dft_known_reference():
    x = np.array([1,5,3,-2,8,9,2,-1],dtype=np.int32)
    n = len(x)
    nf = 11
    w = .5-.5*np.cos(2*np.pi*np.arange(n)/n)
    centered = x.astype(float)-np.mean(x)
    z = np.array([sum(centered[j]*w[j]*np.exp(-2j*np.pi*k*j/nf) for j in range(n)) for k in range(nf//2+1)])
    expected = abs(z)**2/(125*sum(w*w));expected[1:] *= 2
    actual,_,_ = periodograms(x,125,nf)
    np.testing.assert_allclose(actual,expected,rtol=1e-12,atol=1e-12)


def test_integer_extremes_safe_and_zero():
    a = np.array([np.iinfo(np.int32).min,np.iinfo(np.int32).max,0],dtype=np.int32)
    np.testing.assert_allclose(prepare(a,"constant"),a.astype(float)-np.mean(a.astype(float)),atol=1e-7)
    for c in [0,1_700_000_000,-1_700_000_000]:
        x = np.full((3,800),c,dtype=np.int32)
        assert np.max(np.abs(prepare(x,"constant"))) == 0
        p,e,_ = periodograms(x,200,800)
        assert np.max(p) == 0 and np.max(e) == 0
        p,e,_ = periodograms(x,200,800,"none")
        np.testing.assert_allclose(np.sum(p,axis=-1)*.25,c*c,rtol=1e-10,atol=1e-12)


def test_exact_statistics():
    x = np.array([1.,2.,8.,13.,np.nan])
    result = describe(x)
    q = np.quantile(x[:4],[.05,.25,.5,.75,.95],method="linear")
    np.testing.assert_array_equal([result[k] for k in ["q05","q25","median","q75","q95"]],q)
    assert result["valid_count"] == 4 and result["excluded_count"] == 1
    assert result["equivalent_rms"] == np.sqrt(np.mean(x[:4]**2))


@pytest.mark.parametrize("rate",[125,200,1000])
@pytest.mark.parametrize("delta",[0,1,123])
def test_mlx_guarded_psd(rate,delta):
    pytest.importorskip("mlx.core")
    x = signal_fixture(rate,10,12)
    n = rate*4
    segments = np.lib.stride_tricks.sliding_window_view(x,n,axis=-1)[:,::n//2,:]
    ref,energy,_ = periodograms(segments,rate,n+delta)
    actual,_,report = periodograms(segments,rate,n+delta,backend="mlx")
    assert report["executed"] == "mlx",report
    errors = spectral_errors(actual,ref,rate/(n+delta))
    assert errors["relative_integrated_error"] <= 1e-4
    assert errors["max_supported_bin_db_error"] <= .05
    assert errors["zero_residual_power"] <= 1e-12
    np.testing.assert_allclose(np.sum(actual,axis=-1)*rate/(n+delta),energy,rtol=1e-4,atol=1e-12)
    prepared = prepare(segments,"constant")
    error = np.sqrt(np.mean((prepared.astype(np.float32).astype(float)-prepared)**2,axis=-1))
    rms = np.sqrt(np.mean(prepared**2,axis=-1))
    assert np.all(error <= 1e-5*rms+1e-6*(rms==0))


def test_gpu_failure_falls_back_without_changing_values(monkeypatch):
    mx=pytest.importorskip("mlx.core")
    x=signal_fixture(200,10,4)[:,None,:]
    expected,_,_=periodograms(x,200,800)
    def fail(*args,**kwargs):raise MemoryError("injected device allocation failure")
    monkeypatch.setattr(mx.fft,"rfft",fail)
    actual,_,report=periodograms(x,200,800,backend="mlx")
    np.testing.assert_array_equal(actual,expected)
    assert report["executed"]=="cpu" and "MemoryError" in report["fallback"]
    constant=np.full((1,800),1_700_000_000,dtype=np.int32)
    _,_,report=periodograms(constant,200,800,"none",backend="mlx")
    assert report["executed"]=="cpu" and "DC-preserving" in report["fallback"]
