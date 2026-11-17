"""Build a self-contained Windows x64 directory; no user data is collected."""

import subprocess
import sys
from importlib import metadata
from pathlib import Path
from shutil import copy2

ROOT = Path(__file__).resolve().parents[1]


def copy_licenses(runtime):
    """Keep redistribution notices alongside the bundled runtime, not in user data."""
    target = runtime / "_internal" / "licenses"
    target.mkdir(parents=True, exist_ok=True)
    copy2(Path(sys.base_prefix) / "LICENSE.txt", target / "Python-LICENSE.txt")
    for package in ("Pillow", "PyInstaller"):
        distribution = metadata.distribution(package)
        for path in distribution.files or ():
            if "license" in path.name.lower() or "copying" in path.name.lower():
                copy2(distribution.locate_file(path), target / f"{package}-{path.name}")
    for path in (Path(sys.base_prefix) / "tcl").glob("*/license.terms"):
        copy2(path, target / f"{path.parent.name}-license.terms")


def main():
    if sys.platform != "win32":
        raise SystemExit("请在Windows上运行此构建脚本")
    output = ROOT / ".local" / "windows"
    work = ROOT / ".local" / "pyinstaller"
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--windowed",
            "--name",
            "拾光二手交易系统",
            "--paths",
            str(ROOT / "src"),
            "--distpath",
            str(output),
            "--workpath",
            str(work / "work"),
            "--specpath",
            str(work),
            str(ROOT / "run.py"),
        ],
        cwd=ROOT,
        check=True,
    )
    copy_licenses(output / "拾光二手交易系统")
    print(f"运行目录：{output / '拾光二手交易系统'}")
    print("分发整个目录，不能只复制exe；首次启动创建管理员和本机用户数据。")


if __name__ == "__main__":
    main()
