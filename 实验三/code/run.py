"""Source-tree entry point: run without installing the project package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    from marketplace.__main__ import main

    main()
