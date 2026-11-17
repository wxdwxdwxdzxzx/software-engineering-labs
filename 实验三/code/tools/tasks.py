"""Reproducible local verification commands; nothing in this file pushes Git."""

import argparse
import ast
import importlib.metadata
import io
import json
import os
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".local" / "checks"


def invoke(arguments, output):
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    combined = result.stdout + result.stderr
    (EVIDENCE / output).write_text(combined, encoding="utf-8")
    print(combined, end="")
    if result.returncode:
        raise SystemExit(result.returncode)
    return combined


def effective_lines(path):
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    ignored = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            doc = node.body[0]
            ignored.update(range(doc.lineno, doc.end_lineno + 1))
    comments = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            comments[token.start[0]] = token.start[1]
    valid = 0
    for number, line in enumerate(source.splitlines(), 1):
        if number not in ignored and line[: comments.get(number, len(line))].strip():
            valid += 1
    return {"physical": len(source.splitlines()), "effective": valid}


def count():
    totals = {
        "backend": 0,
        "handwritten_ui": 0,
        "entrypoint": 0,
        "tools": 0,
        "interface_stubs": 0,
        "gui_generated": 0,
    }
    files = {}
    for folder in ["src", "tools"]:
        for path in sorted((ROOT / folder).rglob("*.py")):
            key = str(path.relative_to(ROOT)).replace("\\", "/")
            counts = effective_lines(path)
            group = (
                folder
                if folder == "tools"
                else (
                    "handwritten_ui"
                    if path.name.startswith("ui")
                    else "entrypoint"
                    if path.name == "__main__.py"
                    else "interface_stubs"
                    if path.name == "contracts.py"
                    else "backend"
                )
            )
            totals[group] += counts["effective"]
            files[key] = {**counts, "group": group}
    bootstrap = effective_lines(ROOT / "run.py")
    totals["entrypoint"] += bootstrap["effective"]
    files["run.py"] = {**bootstrap, "group": "entrypoint"}
    result = {
        "method": "Python tokenize comments + AST docstring ranges; exclude blank lines. "
        "SQLite DDL strings count as handwritten business source. "
        "UI, tools and protocol stubs are reported separately; tests are excluded.",
        "effective_totals": totals,
        "files": files,
    }
    (EVIDENCE / "code-size.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(totals, ensure_ascii=False, indent=2))
    assert totals["backend"] > 1500


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=["lint", "build", "count", "all"])
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    versions = {
        name: importlib.metadata.version(name)
        for name in ["ruff", "build", "setuptools", "wheel", "Pillow"]
    }
    (EVIDENCE / "tool-versions.json").write_text(
        json.dumps({"python": sys.version, "tools": versions}, indent=2), encoding="utf-8"
    )
    tasks = ["lint", "build", "count"] if args.task == "all" else [args.task]
    for task in tasks:
        if task == "lint":
            invoke(["-m", "ruff", "check", "src", "tools", "run.py"], "lint.txt")
            invoke(["-m", "ruff", "format", "--check", "src", "tools", "run.py"], "format.txt")
            invoke(["-m", "compileall", "-q", "src"], "compile.txt")
        elif task == "build":
            invoke(["-m", "build", "--no-isolation", "--outdir", ".local/dist"], "build.txt")
        else:
            count()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
