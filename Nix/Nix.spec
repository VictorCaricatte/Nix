# -*- mode: python ; coding: utf-8 -*-
# Build em modo PASTA (onedir): abre bem mais rápido que o .exe único e é muito
# menos marcado por antivírus/SmartScreen. Use installer/build_installer.ps1.


a = Analysis(
    ['Nix.py'],
    pathex=[],
    binaries=[],
    datas=[('Nix.jpg', '.'), ('Nix.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'jupyter_client', 'jupyter_core',
              'pytest', 'scipy', 'notebook', 'sphinx'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Nix',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # exe comprimido com UPX dispara antivírus
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['Nix.ico'],
    version='version_info.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Nix',
)
