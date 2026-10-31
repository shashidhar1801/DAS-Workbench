# DAS Workbench 0.3.4.post1

This release package adds an illustrated tutorial for the manuscript examples and improves public distribution of the existing v0.3.4 application.

## Included

- A 16-page guide with genuine GUI screenshots, exact example selections and recipes, installation instructions, verification, export/reopen, Stop and spectral checkpoint/resume guidance.
- Svalbard, Mediterranean, Lake Zurich and Trondheimsfjord tutorials; the Mediterranean window comparison; and bounded OOI CPU/Apple-GPU and full-fibre overview tutorials.
- MIT application source, tests, independent reference code, dependency notices and author citation metadata.
- A separately packaged Apple Silicon Mac app and historical example exports with numerical products and scoped validation evidence.

The application-code changes are limited to version and About text. The package omits an unused Qt Virtual Keyboard component and includes upstream Qt/PySide licence texts and corresponding-source access. Scientific modules, native quantity definitions and OOI checkpoint/resume behavior are unchanged.

## Validation and limitations

The source suite passed all 102 tests. The frozen package passed its offscreen synthetic maintenance checks and strict signature verification. Nine historical saved-result folders passed reopening/integrity checks. These checks do not replace fresh source-based real-recording analysis, independent installation or usability testing. The application is ad-hoc signed and not notarized. See [validation scope](docs/VALIDATION.md).

## Downloads

Choose the app ZIP and PDF guide for normal use. The source ZIP is for inspection and rebuilding. The example-bundle ZIP allows reopening historical saved results without downloading all original recordings. Original recordings are needed for reanalysis and fresh source-based verification.

The Qt and PySide source archives are separate corresponding-source downloads. They are not needed to launch the app or read the guide. All release files have SHA256 fingerprints in the accompanying manifest. Raw multi-gigabyte recordings, the terabyte-scale OOI collections, manuscript drafts, credentials and build caches are excluded.

Repository: https://github.com/shashidhar1801/DAS-Workbench. No archival DOI is claimed.
