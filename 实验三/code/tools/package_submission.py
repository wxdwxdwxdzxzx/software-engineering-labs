"""Build a persistent allowlisted staging tree, then archive only that tree."""

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
NAME = "实验3_241220020_王旭东"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=CODE / ".local/windows/拾光二手交易系统")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.output / NAME
    if root.exists():
        parser.error("暂存目录已存在；请选择新的输出目录，避免覆盖已有材料")
    root.mkdir()
    shutil.copy2(CODE.parent / "实验报告.pdf", root / "实验报告.pdf")
    uml = root / "UML"
    uml.mkdir()
    for source in sorted((CODE.parent / "UML").glob("*.puml")):
        for path in (source, source.with_suffix(".png")):
            shutil.copy2(path, uml / path.name)
    target = root / "code"
    target.mkdir()
    for name in (
        "run.py",
        "pyproject.toml",
        "MANIFEST.in",
        "requirements-dev.txt",
        "requirements-exe.txt",
        "README.md",
    ):
        shutil.copy2(CODE / name, target / name)
    for folder in ("src/marketplace",):
        for source in (CODE / folder).glob("*.py"):
            dest = target / source.relative_to(CODE)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
    (target / "tools").mkdir()
    for name in ("tasks.py", "build_windows.py", "package_submission.py"):
        shutil.copy2(CODE / "tools" / name, target / "tools" / name)
    assert (args.runtime / "拾光二手交易系统.exe").is_file()
    assert (args.runtime / "_internal").is_dir()
    shutil.copytree(args.runtime, target / "运行程序")
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    forbidden = {".sqlite", ".db", ".log", ".pyc", ".whl", ".tar", ".gz"}
    for path in paths:
        assert path.suffix.lower() not in forbidden, path
        assert not {"docs", "tests", "__pycache__", ".git", ".venv", ".local"}.intersection(
            path.relative_to(root).parts
        ), path
    output = args.output / f"{NAME}.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in paths:
            archive.write(path, (Path(NAME) / path.relative_to(root)).as_posix())
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert {p.split("/")[0] for p in archive.namelist()} == {NAME}
        assert {p.split("/")[1] for p in archive.namelist()} == {"实验报告.pdf", "UML", "code"}
    print(
        json.dumps(
            {
                "zip": str(output),
                "files": len(paths),
                "bytes": output.stat().st_size,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
