# Manuscript examples

The recipes in `recipes/` reproduce the manuscript's native sample and channel selections. Open the matching recording first, then use **Load settings…**. Recipe filenames here are descriptive; the original development labels were F02 (Svalbard), F01 (Mediterranean), F04 (Lake Zurich), F03 (Trondheimsfjord), and S01 (window comparison).

The separate release asset `DAS_Workbench_Example_Bundles.zip` contains historical v0.3.3 GUI exports with their original manifests, numerical arrays, settings, captions and validation sidecars. Open a case folder using **Open saved result…**. Its historical version and validation dates are intentionally preserved. Reopening a bundle does not rerun its analysis. To perform fresh numerical verification, also open the exact original recording.

## Recordings and attribution

| Example | Public source and creators | Exact selected file | Local SHA256 fingerprint |
|---|---|---|---|
| Svalbard | Bouffaut and Taweesintananon, DAS4Whale, [Zenodo 5823343](https://doi.org/10.5281/zenodo.5823343) | `20200627_192255_ch05001_to_ch07000_whale_raw_L310s.mat` | `e8e91c57232a4126c8857e06fd30e25b05557ea84423e2d52bda22aaabdbedf7` |
| Mediterranean | Bouffaut, Goestchel, Sladen, Hartog and Klinck, [Zenodo 15008561](https://doi.org/10.5281/zenodo.15008561) | `DAS_MedSea/data/091304.hdf5` | `a430a81533be0a88da65981ecf5f0dea67d74d49fec14f32f3ce090c95607e04` |
| Trondheimsfjord | Thiem, Vaupel, Wienecke, Eidsvik, Taweesintananon and Landrø, [Zenodo 18851481](https://doi.org/10.5281/zenodo.18851481) | `Trondheim_DAS_Data/085437.hdf5` | `6908a9d9d0576de02a386449a17502fbc290f5eb29ac7ed6c28116bcaf382655` |
| Lake Zurich | Rychen, Paitz, Edme, Smolinski, Brackenhoff and Fichtner, [Zenodo 7886409](https://doi.org/10.5281/zenodo.7886409) | `2022-01-26--04--Whales/2022-01-26--04-46-16--Humpback.h5` | `ccc1e437ab35912018c3de1a1fed5ef56789095dce515bd4658a45ea940a110d` |

All four pinned source records were distributed under **CC BY 4.0** in the acquisition metadata. See `source_citations.json` for complete creator records. Derived plots, screenshots containing those plots and numerical example products retain that attribution and licence; they are selected, mean-removed and spectrally transformed products, not unmodified recordings. No endorsement by the source authors is implied. Application interface code and original guide prose are MIT licensed. See [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

The four waveform files occupy approximately 3.83 GB together; Svalbard alone is 3.20 GB. The app and source downloads do not contain these recordings. Use the source record's file list to download the named file or extract the named archive member. Mediterranean and Trondheim parent archives may be large. Do not assume that downloading an entire source collection is necessary for this tutorial. Obtain the supporting README, annotations and calibration/geometry documentation where available. The manifest in this directory records selected paths and fingerprints.

## Exact accepted selections

Samples and channel indices below are zero based. Sample intervals and `a:b` channel syntax are half-open: include `a`, exclude `b`. Coordinate section bounds in the GUI are inclusive. Use the supplied recipe when exact reproduction matters.

| Case | Native samples | Array channels | Trace | Window and hop request | Display settings |
|---|---|---|---|---|---|
| Svalbard | `[25806,77419)` | `0:2000` | 1999 | 2 s / 0.2 s | 100 Hz maximum; bin 10; stride 10; shore-distance coordinate |
| Mediterranean | `[0,727)` | `748:13000` | 8663 | 1 s / 0.1 s | 35 Hz maximum; bin 1; stride 16 |
| Trondheimsfjord | `[0,5682)` | `64:10500` | 500 | 0.5 s / 0.05 s | 250 Hz maximum; bin 5; stride 16 |
| Lake DAS | `[50,150050)` | `0:946` | 820 | 0.1 s / 0.05 s | 2500 Hz maximum; bin 50; stride 4; channel coordinate |
| Lake received pressure | `[393007,1893007)` | `0:4` | 0 | 0.1 s / 0.05 s | 2500 Hz maximum; bin 500; stride 1; 1 s received-level windows |
| Lake nominal source | `[393007,1893007)` | `0:1` | 0 | 0.1 s / 0.05 s | 2500 Hz maximum; bin 500; stride 1 |

`mediterranean_window_comparison.json` repeats the same selection with 1 s and 4 s requested windows. Actual sample rounding and window durations are shown by **Review analysis settings**. The comparison changes spectral smoothing, not the underlying selection.

## OOI collections

Provider: [Ocean Observatories Initiative DAS experiment](https://oceanobservatories.org/pi-instrument/rapid/). The evaluated North Cable records are OptaSense processed-phase HDF5, not pressure. The provider's original file directories are recorded in `ooi_sources.json` without the author's local disk paths.

- 2021-11-04 at 200 Hz: 120 first-two-hour files plus 600 additional-ten-hour files; 720 files form the evaluated 12 h run.
- 2021-11-02 at 1000 Hz: 240 files form the evaluated 2 h run.
- Both have 32,600 channels and approximately 2.041904688 m channel spacing. Gauge lengths differ: approximately 51.047619 m and 30.628572 m.
- First example filenames: `North-C1-LR-P1kHz-GL50m-Sp2m-FS200Hz_2021-11-04T000002Z.h5` and `North-C3-HF-P1kHz-GL30m-Sp2m_2021-11-02T000015Z.h5`.

Start with one file for the tutorial. The full 960-file evaluation is approximately 1.065 TB and is not required for installation or learning the controls. Follow the provider's data-use and citation policy. The repository does not relicense or redistribute raw OOI recordings.
