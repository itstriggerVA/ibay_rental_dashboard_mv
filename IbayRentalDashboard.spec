# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build recipe for the portable desktop launcher.

Build with:
    .\\.venv\\Scripts\\python.exe -m PyInstaller --clean --noconfirm IbayRentalDashboard.spec

Prefer scripts/build_portable.ps1 for release builds because it also prepares
the runtime data folders and copies the seed dataset/import files.

The project intentionally uses a one-folder build instead of one-file
extraction. It keeps startup predictable, makes the contents inspectable, and
avoids writing bundled binaries to a temporary directory at runtime.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


block_cipher = None
project_root = Path.cwd()

datas = [
    (str(project_root / "dashboard"), "dashboard"),
    (str(project_root / "scrapy.cfg"), "."),
]
binaries = []
hiddenimports = collect_submodules("ibay_rentals")

seed_dataset = project_root / "data" / "processed" / "ibay_rentals_master.csv.gz"
if seed_dataset.exists():
    datas.append((str(seed_dataset), "data/processed"))

schema_aligned_imports = project_root / "data" / "imports" / "schema_aligned"
if schema_aligned_imports.exists():
    datas.append((str(schema_aligned_imports), "data/imports/schema_aligned"))

for package in ("streamlit", "plotly", "altair", "openpyxl"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

for package in (
    "Scrapy",
    "lxml",
    "cssselect",
    "parsel",
    "w3lib",
    "Twisted",
    "pyOpenSSL",
    "cryptography",
    "service-identity",
    "protego",
    "itemadapter",
    "itemloaders",
    "tldextract",
):
    datas += copy_metadata(package, recursive=True)

hiddenimports += [
    "pyarrow",
    "pyarrow.csv",
    "pyarrow.dataset",
    "pyarrow.fs",
    "pyarrow.parquet",
    "scrapy.pipelines.files",
    "scrapy.pipelines.images",
    "twisted.internet.asyncioreactor",
    "twisted.internet.selectreactor",
]

excludes = [
    "IPython",
    "jupyter",
    "matplotlib",
    "notebook",
    "pytest",
    "scipy",
    "tests",
]

a = Analysis(
    ["src/ibay_rentals/desktop.py"],
    pathex=[str(project_root / "src"), str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IbayRentalDashboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

worker_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IbayRentalWorker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    worker_exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="IbayRentalDashboard",
    contents_directory=".",
)
