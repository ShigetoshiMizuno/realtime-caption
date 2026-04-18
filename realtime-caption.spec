# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

datas = [('overlay.html', '.')]
binaries = []
hiddenimports = [
    'torch',
    'torch.nn',
    'torch.nn.functional',
    'faster_whisper',
    'RealtimeSTT',
    'pyaudiowpatch',
    'websockets',
    'websockets.legacy',
    'websockets.legacy.server',
    'openai',
    'scipy.signal',
    'scipy.signal._upfirdn',
    'numpy',
    'yaml',
    'torchaudio',
    'huggingface_hub',
    'ctypes',
    'ctypes.util',
]

for pkg in ('torch', 'torchaudio', 'faster_whisper', 'RealtimeSTT'):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='realtime-caption',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='realtime-caption',
)
