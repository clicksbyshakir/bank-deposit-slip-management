"""Build the Windows app and its installer.

1. PyInstaller bundles the app into dist/DepositSlipTracker/ (a folder, so it starts fast).
2. If Inno Setup is installed, it wraps that folder into dist/DepositSlipTracker-Setup-<version>.exe.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "deposit_tracker" / "icon.ico"
ISCC_PATHS = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
]


def app_version() -> str:
    text = (ROOT / "deposit_tracker" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def main() -> None:
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
        "--name", "DepositSlipTracker",
        "--icon", str(ICON),
        "--add-data", f"{ICON}{os.pathsep}deposit_tracker",
        "--collect-submodules", "pillow_heif",
        "--collect-data", "rapidocr_onnxruntime",
        str(ROOT / "run.py"),
    ], cwd=ROOT, check=True)
    print(f"\nApp folder: {ROOT / 'dist' / 'DepositSlipTracker'}")

    iscc = shutil.which("iscc") or next((str(p) for p in ISCC_PATHS if p.exists()), None)
    if not iscc:
        print("Inno Setup not found - skipping the installer. "
              "Get it from https://jrsoftware.org/isdl.php to build Setup.exe.")
        return
    subprocess.run([iscc, f"/DMyAppVersion={app_version()}",
                    str(ROOT / "packaging" / "installer.iss")], check=True)
    print(f"Installer: {ROOT / 'dist' / f'DepositSlipTracker-Setup-{app_version()}.exe'}")


if __name__ == "__main__":
    main()
