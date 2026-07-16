from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"


def run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> None:
    run(["npm", "ci"], cwd=WEB_DIR)
    run(["npm", "run", "build"], cwd=WEB_DIR)


if __name__ == "__main__":
    main()
