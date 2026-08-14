"""Build script to package Alpha Research as a standalone executable via PyInstaller."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    spec_file = backend_dir / "alpha_research.spec"

    print("==================================================")
    print("   Building Alpha Research Standalone Desktop Executable")
    print("==================================================")
    print(f"Backend directory: {backend_dir}")
    print(f"Spec file: {spec_file}")

    if not spec_file.exists():
        print(f"Error: Spec file not found at {spec_file}")
        sys.exit(1)

    # Check if pyinstaller is available
    pyinstaller_bin = shutil.which("pyinstaller")
    if not pyinstaller_bin:
        # Check inside venv
        venv_pyinstaller = backend_dir / ".venv" / "bin" / "pyinstaller"
        if venv_pyinstaller.exists():
            pyinstaller_bin = str(venv_pyinstaller)
        else:
            print("PyInstaller not found! Please run:")
            print("  uv pip install pyinstaller")
            sys.exit(1)

    print(f"Using PyInstaller at: {pyinstaller_bin}")

    cmd = [pyinstaller_bin, "--noconfirm", str(spec_file)]
    print(f"Running command: {' '.join(cmd)}")

    res = subprocess.run(cmd, cwd=str(backend_dir))
    if res.returncode != 0:
        print(f"\nBuild failed with exit code {res.returncode}")
        sys.exit(res.returncode)

    dist_dir = backend_dir / "dist"
    exe_name = "alpha-research-desktop.exe" if os.name == "nt" else "alpha-research-desktop"
    exe_path = dist_dir / exe_name

    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print("\n==================================================")
        print("   SUCCESS! Standalone Desktop Executable Built")
        print("==================================================")
        print(f"Executable Location: {exe_path}")
        print(f"File Size: {size_mb:.2f} MB")
        print("\nTo run the desktop app:")
        print(f"  {exe_path}")
    else:
        print(f"\nError: Expected output file at {exe_path} was not found.")
        sys.exit(1)


if __name__ == "__main__":
    main()
