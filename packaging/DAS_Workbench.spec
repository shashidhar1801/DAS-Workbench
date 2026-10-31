# Build natively on the target platform. Windows output remains untested until
# this spec is executed and verified on an actual Windows host.
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata
root = Path(SPECPATH).parent

datas = [(str(root/'LICENSE'),'licenses')]
datas += [(str(root/'packaging/qt_notices'),'licenses/qt_notices')]
from importlib.metadata import distribution
# Preserve every installed runtime distribution licence in the bundled notices.
for pkg in ['numpy','scipy','h5py','matplotlib','PySide6','shiboken6','PySide6_Essentials','PySide6_Addons','pyqtgraph','psutil','mlx','mlx-metal','pillow','fonttools','contourpy','cycler','kiwisolver','pyparsing','python-dateutil','six','packaging']:
    dist = distribution(pkg)
    for f in dist.files or []:
        if any(token in str(f).lower() for token in ('license','copying','notice')) and dist.locate_file(f).is_file():
            datas.append((str(dist.locate_file(f)), 'licenses/'+pkg+'/'+str(Path(str(f)).parent)))
datas += [(str(root/'validation/public_reference.py'),'validation_source')]
for package in ['numpy','scipy','h5py','matplotlib','PySide6','pyqtgraph','psutil']:
    datas += copy_metadata(package)
hiddenimports = ['scipy.signal','scipy.fft','scipy.special','matplotlib.backends.backend_agg',
                 'matplotlib.backends.backend_pdf','matplotlib.backends.backend_svg','matplotlib.backends.backend_qtagg']
binaries = []
if sys.platform == 'darwin':
    datas += copy_metadata('mlx')
    datas += collect_data_files('mlx')
    binaries += collect_dynamic_libs('mlx')
    # MLX's native extension imports these modules dynamically during NB_MODULE.
    hiddenimports += ['mlx.core','mlx._reprlib_fix','mlx.__array_api_info']
a = Analysis([str(root/'launch.py')], pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports,
             excludes=['PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtPdf',
                       'PySide6.QtQml','PySide6.QtQuick','tkinter','IPython','pytest'],
             noarchive=False)
# QtGui's hook collects all installed input plugins, including an unused
# GPL-only virtual keyboard. This desktop application uses native input.
# Exclude its plugin, QML resources and dependent frameworks from distribution.
def public_runtime_entry(entry):
    return not any(token in str(entry[0]).lower() or token in str(entry[1]).lower()
                   for token in ('virtualkeyboard', 'qtvirtualkeyboard'))
a.binaries = [entry for entry in a.binaries if public_runtime_entry(entry)]
a.datas = [entry for entry in a.datas if public_runtime_entry(entry)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='DAS Workbench',
          debug=False, strip=False, upx=False, console=False, target_arch='arm64' if sys.platform=='darwin' else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='DAS Workbench')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='DAS Workbench.app', bundle_identifier='org.dasresearch.workbench',
                 info_plist={'NSHighResolutionCapable':True,'CFBundleShortVersionString':'0.3.4','CFBundleVersion':'30402',
                             'NSHumanReadableCopyright':'Copyright 2026 DAS Workbench contributors. MIT; dependency licences retained.'})
