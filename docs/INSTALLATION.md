# Installation and building

## Packaged Mac application

The packaged release targets Apple Silicon (`arm64`) macOS. It was built and checked on the author's Mac; it has not been tested on an independent clean machine. Do not infer a tested minimum macOS version from the build's deployment metadata.

1. Download `DAS_Workbench_0.3.4.post1_macOS_arm64.zip` and the release `SHA256SUMS.txt`.
2. In Terminal, run `shasum -a 256 DAS_Workbench_0.3.4.post1_macOS_arm64.zip` and compare the full value with the manifest.
3. Extract the archive. Copy `DAS Workbench.app` to Applications or a user-writable Applications folder. Launch that exact copy; older copies share the application identifier.
4. The package has an ad-hoc signature and is **not notarized**. macOS may require the user to review it under System Settings > Privacy & Security > Open Anyway after an initial launch attempt. Make that trust decision yourself. Do not disable Gatekeeper globally or use recursive quarantine-removal commands.
5. Choose a writable output folder with sufficient space. Source recordings are read-only. For external drives, keep the volume mounted during analysis and verification.

No Python installation is needed for the packaged app. The source route below is available if you prefer to inspect and run the code locally.

## Run from source

Use Python 3.12 on Apple Silicon macOS for the evaluated environment. From the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[metal,test,build]'
python -m das_workbench
```

The exact historical build environment is recorded in `packaging/requirements-macos-arm64.lock.txt`. For a matching environment, install that lock file before the editable package. Some optional Qt packages in the lock are build-environment dependencies and are not all included in the app. Changing pinned versions requires new verification; it is not covered by the historical numerical acceptance records.

For native recordings, the application also exposes `python -m das_workbench public-gui`. The ordinary entry window opens the same native workspace through **Recording analysis…**. Use `python -m das_workbench --help` for supported command-line functions.

## Tests and rebuilding

```sh
QT_QPA_PLATFORM=offscreen python -m pytest tests -q
python -m PyInstaller --noconfirm --clean packaging/DAS_Workbench.spec
codesign --verify --deep --strict 'dist/DAS Workbench.app'
```

The spec builds on the target platform and bundles an inspectable on-disk library layout. Its public-distribution filter omits the unused Qt virtual-keyboard component. Check the final bundle for that component and verify the signature after every packaging change. Re-run the tests and real-data checks after replacing a dependency.

## Replaceable dependencies

Qt/PySide are dynamically loaded libraries. No restriction is imposed on modifying them or reverse engineering the application to debug such modifications. The simplest replacement route is to build the upstream dependency version you want, install it in a separate Python environment, install this application from source, and rebuild the bundle with the supplied spec. Replacing libraries inside an existing Mac bundle invalidates its signature; rebuild or ad-hoc sign the modified copy as appropriate. Preserve all dependency notices.

Exact upstream Qt and PySide source locations and local licence texts are listed in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). Raw datasets and the author's external-drive layout are not needed to install the software.
