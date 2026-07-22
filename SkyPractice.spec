# -*- mode: python ; coding: utf-8 -*-
# Build a standalone app (ONEDIR mode = fast launch, no per-run unpacking):
#   pip install -r requirements.txt pyinstaller
#   pyinstaller SkyPractice.spec
#
# Output:
#   Windows -> dist/SkyPractice/SkyPractice.exe   (ship the whole folder)
#   macOS   -> dist/SkyPractice.app
#   Linux   -> dist/SkyPractice/SkyPractice
#
# Onedir launches far faster than onefile because the bootloader doesn't have to
# unpack everything to a temp folder on every start. On Windows, distribute the
# entire dist/SkyPractice folder (zip it); the .exe needs the files beside it.

import sys

block_cipher = None

a = Analysis(
    ['sky_practice.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'pynput', 'pynput.keyboard', 'pynput.mouse',
        # macOS trust check + window level (ignored if absent on other OSes)
        'ApplicationServices', 'Quartz', 'AppKit', 'objc',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # trim modules we never use -> smaller bundle, faster cold start
        'tkinter', 'unittest', 'pydoc', 'pdb', 'test',
        'PyQt6.QtQml', 'PyQt6.QtQuick', 'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebEngineWidgets', 'PyQt6.QtMultimedia', 'PyQt6.Qt3DCore',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,       # ONEDIR: binaries go into COLLECT, not the exe
    name='SkyPractice',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,               # no terminal window (GUI app)
    disable_windowed_traceback=False,
    argv_emulation=False,        # keep off — can interfere with pynput on macOS
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SkyPractice',
)

# macOS: wrap the onedir output into a .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='SkyPractice.app',
        icon=None,
        bundle_identifier='com.local.skypractice',
        info_plist={
            'NSHighResolutionCapable': True,
            'LSUIElement': False,
            # Required so macOS shows the permission prompts for an unsigned app.
            'NSAppleEventsUsageDescription':
                'Sky Practice watches your key presses to follow along with the song.',
            'NSInputMonitoringUsageDescription':
                'Sky Practice needs to see key presses to light up the pads even when '
                'another window (like the game) is focused.',
        },
    )
