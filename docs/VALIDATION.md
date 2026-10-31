# Validation and release scope

This packaging revision is **0.3.4.post1**, prepared on 31 October 2026. It does not introduce new scientific calculations. The release checks below are distinct from historical real-recording validation and from independent usability testing.

## Checks executed for this revision

| Check | Outcome | Evidence and limits |
|---|---|---|
| Source regression suite | 102 passed, 28 existing Qt deprecation warnings | `validation/pytest.txt` and `validation/pytest.xml`; offscreen Qt, 7.65 s on the author's Mac |
| Application-code comparison with v0.3.4 | 22 modules byte-identical; only version and About text changed in two modules | [Release audit](RELEASE_AUDIT.json); source hashes retained |
| Packaged synthetic maintenance checks | Passed | `validation/Focused_Runtime_Checks.json`; frozen application, offscreen Qt, timestamp and error-recovery fixtures; not a real-recording desktop run |
| Saved example reopening and integrity | Nine saved-result folders passed | [Release audit](RELEASE_AUDIT.json); application loader checks numeric, recipe and scope hashes and their historical validation sidecars |
| App signature integrity, including extraction of the release ZIP | Passed | Strict, deep `codesign` verification; ad-hoc signature, **not Apple notarization** |
| Dependency packaging | Unused Qt Virtual Keyboard absent; Qt/PySide notice files present | [Release audit](RELEASE_AUDIT.json) and [dependency notices](../THIRD_PARTY_NOTICES.md) |
| Illustrated guide | 16 pages rendered and visually checked | Final PDF and editable Word document supplied separately; screenshot origins in [SCREENSHOT_PROVENANCE.json](SCREENSHOT_PROVENANCE.json) |

The saved-example audit reopens the four case exports, their four overview exports, and the Mediterranean window comparison. Their v0.3.3 dates, versions and scoped reference-comparison results remain unchanged. Reopening and hash verification do **not** constitute a new numerical calculation from the original recordings.

The independent reference implementation remains available in `validation/public_reference.py`; the OOI reference and gate implementations remain in `validation/` and `das_workbench/`. Run the GUI's numerical verification on the exact selected recording to create a fresh source-based comparison. A passed sidecar applies to its recorded selection, quantity, settings and numerical products. It does not certify another selection or independently establish event identity or calibration.

Public copies of execution logs replace the author's home/workspace paths with placeholders; the original local logs are retained.

## Checks not established by this revision

- The new package was launched interactively and its About dialog confirmed 0.3.4.post1. The native workspace opened. After selecting a saved Mediterranean result, desktop observation timed out; that GUI reopening is not marked passed. A full fresh interactive run through all manuscript cases remains unverified. The guide uses genuine historical packaged-GUI captures with their original versions recorded. Offscreen tests are explicitly separate.
- Independent clean-machine installation, user testing, Apple Developer ID signing/notarization, Windows/Intel Mac/Linux packaged execution, and full-core GPU occupancy are not established.
- The historical 960-file OOI benchmark and real-recording numerical matrix were not rerun for this packaging-only change. No new throughput or numerical-agreement claim is made.
- Public GitHub creation and release publication must be confirmed by a visible repository, tag and downloadable assets. A prepared local directory is not evidence of publication.

## Reproduce the checks

Use the installation instructions and pinned environment, then run:

```sh
QT_QPA_PLATFORM=offscreen python -m pytest tests -q --junitxml=pytest.xml
QT_QPA_PLATFORM=offscreen 'dist/DAS Workbench.app/Contents/MacOS/DAS Workbench' maintenance-check --output maintenance-check
python -m validation.distribution_audit --help
```

The distribution audit accepts the previous source, new app and extracted example folders. Its hash comparison requires the actual v0.3.4 source; it is not meaningful against an arbitrary checkout. Preserve original reports and produce a new output directory for each validation run.
