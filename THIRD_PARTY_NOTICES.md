# Dependency notices and corresponding source

DAS Workbench application code is MIT licensed. Dependencies retain their own notices and terms. The release does not claim that the entire binary bundle is covered by MIT alone.

The Mac app contains notice files under `DAS Workbench.app/Contents/Resources/licenses`. This repository also includes `packaging/third_party_licenses/` and `packaging/qt_notices/`. The build spec collects installed distribution notices and the additional upstream Qt/PySide licence texts. The public package omits the unused Qt Virtual Keyboard plugin and frameworks, which have a separate GPLv3 licensing option. Other dynamically loaded Qt/PySide components are supplied under their LGPL-compatible open-source terms and component notices.

| Component | Version in evaluated build | Source or distribution |
|---|---|---|
| Qt | 6.12.0 | [Exact upstream Qt source archive](https://download.qt.io/archive/qt/6.12/6.12.0/single/qt-everywhere-src-6.12.0.tar.xz) |
| PySide6 and Shiboken6 | 6.12.0 | [Exact upstream binding source archive](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz) |
| NumPy | 2.5.4 | [NumPy source](https://github.com/numpy/numpy) |
| SciPy | 1.18.0 | [SciPy source](https://github.com/scipy/scipy) |
| h5py | 3.16.0 | [h5py source](https://github.com/h5py/h5py) |
| Matplotlib | 3.11.2 | [Matplotlib source](https://github.com/matplotlib/matplotlib) |
| pyqtgraph | 0.14.0 | [pyqtgraph source](https://github.com/pyqtgraph/pyqtgraph) |
| MLX and MLX Metal | 0.32.4 | [Apple MLX source](https://github.com/ml-explore/mlx) |
| psutil | 7.2.2 | [psutil source](https://github.com/giampaolo/psutil) |
| PyInstaller bootloader | 6.22.3 | [PyInstaller source and exception](https://github.com/pyinstaller/pyinstaller) |

The two exact Qt source archives are also retained with the local release materials, separately from the small application-source archive. They are unmodified upstream sources. `packaging/upstream_sources.json` records download locations, sizes and locally computed SHA256 fingerprints. Those fingerprints identify the retrieved bytes; they are not described as publisher-provided checksums.

See [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html), [Qt open-source obligations](https://www.qt.io/development/open-source-lgpl-obligations), and the local licence texts. Users may replace LGPL libraries and debug their modifications. [Installation and building](docs/INSTALLATION.md) explains the source/rebuild route. No technological restriction on that replacement is imposed by the application.

Scientific example products are separately attributed in [examples/README.md](examples/README.md). Do not remove the creators, dataset DOIs, licence statements or processing descriptions when redistributing them.
