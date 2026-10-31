# Scientific definitions and limits

## Quantities and coordinates

Native recording analysis uses CPU float64 and retains each supported profile's measurement quantity. OOI stored counts may be shown as counts or converted to phase using the metadata scale of 2π/65536 radians per count. This is not a conversion to strain or pressure.

Svalbard uses source-declared nanostrain. Its **Distance from shore (m)** vector is distinct from its other position vector. The selected block spans 15.64632–23.81263 km from shore and excludes the published 25.1 km shore apex. Do not shift coordinates or place an apex marker at that distance in this block.

Mediterranean data retain phase rate per distance, rad/(s m). The approximately 1.02 m header gauge declaration and 4 m source-documentation value remain unresolved. Neither value is silently selected for calibration.

Trondheimsfjord has conflicting quantity/unit declarations. The workflow retains native amplitude without a quantitative pressure or strain-rate interpretation. The interrogator timestamp is retained without an inferred AIS offset. The short selected recording does not establish a vessel identity, trajectory or complete passage.

Lake Zurich is controlled freshwater playback, not a free-ranging whale observation. DAS values retain publisher scaling and strain-rate units (nm/m)/s; AmpScaling is not applied a second time. Received hydrophone pressure and nominal source pressure at 1 m remain separate. The common interval begins at DAS sample 50, excluding the extreme initial sample by explicit time selection. Source metadata support this interpretation; independent physical calibration is not established. Native rates remain 5 kHz for DAS and 50 kHz for the pressure streams.

## Waveforms and spatial RMS

Native result waveforms and spatial RMS use the selected native samples after removing the selection-wide channel mean. Spatial RMS is the square root of the mean squared mean-removed samples. No bandpass is applied in the case recipes. The waveform mean-removal interval must therefore be stated when comparing amplitudes.

Waterfall channel stride and sample-bin averaging reduce the display. They do not reduce the samples used for the native waveform/RMS statistics. A bin of 1 means no temporal averaging in the waterfall. A bin of 10 or 50 must not be described as unaveraged display. Numerical storage and display reduction are separate choices.

## Spectra

Case recipes use complete Hann-window segments, segment mean removal and one-sided density scaling. The hop controls spacing between window starts; it is not a request to average adjacent spectrogram frames. Welch PSD averages complete segment periodograms. The spectrogram retains individual complete-window estimates. Incomplete trailing segments are not padded.

Actual window length and hop are integer samples obtained from the requested seconds. FFT length controls frequency-bin spacing; zero padding does not create additional physical resolving power. A longer Hann window changes the time-frequency tradeoff and spectral smoothing. The GUI planner displays accepted sample counts, bin spacing and equivalent noise bandwidth.

The integral of a Welch density is not generally identical to a selection-wide, unfiltered mean-removed waveform mean square: window weighting and per-segment detrending differ. Do not treat their difference as a failed validation without matching the definitions.

## Pressure levels

Received-pressure SPL is enabled only for the qualified received-pressure profile. The Lake Zurich 1 s values use mean-removed native pressure and a 1 μPa reference. A 0–2500 Hz spectrogram display does not band-limit those pressure-level calculations; the native 50 kHz stream has a 25 kHz Nyquist frequency. DAS spectral dB, received-pressure dB and nominal-source dB have different reference quantities and cannot be compared as if they shared calibration.

## Numerical verification and backend scope

**Numerical verification** compares saved native products with a separate CPU reference using the matching source. Sidecars bind the data/settings identity and report checks and tolerances. Passed numerical verification does not prove species identity, physical calibration, gauge interpretation, synchronization or source localization. A reopened historical Passed sidecar is historical evidence, not a new run.

The OOI Apple-GPU path runs supported FFT work through MLX at float32, compares against CPU float64 and records acceptance or fallback. CPU work, reading, conversion, transfer/materialization, comparison and output writing remain part of execution. This conservative path can be slower than CPU. Selecting it does not establish GPU occupancy or guarantee use of every core. The native event workspace remains CPU float64.

## Saved products and stopping

Keep `manifest.json`, `recipe.json`, `numeric.h5`, checksums and any validation sidecar together. Exported PNG/PDF/SVG are presentation products. Complete saved analysis products remain available without transient display clipping, but bundles do not contain the entire original recording.

Native recording Stop and OOI overview Stop acknowledge the request, finish bounded in-flight work and leave an interrupted result requiring regeneration. They do not claim instant termination or resumable overview state. OOI spectral Pause/Resume uses checked checkpoints and preserves the existing source/settings identity contract. Do not confuse these two workflows.
