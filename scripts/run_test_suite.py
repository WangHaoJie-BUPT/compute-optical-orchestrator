"""Discover and run every executable project test script."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pattern",
        default="test_*.py",
        help="Glob pattern below scripts/ (default: test_*.py).",
    )
    arguments = parser.parse_args()

    tests = sorted((PROJECT_ROOT / "scripts").glob(arguments.pattern))

    if not tests:
        raise SystemExit(
            f"No tests matched scripts/{arguments.pattern}."
        )

    started_at = time.monotonic()
    failures: list[Path] = []

    for test_file in tests:
        test_started_at = time.monotonic()
        completed = subprocess.run(
            [sys.executable, str(test_file)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        duration = time.monotonic() - test_started_at

        if completed.returncode == 0:
            print(f"[PASS] {test_file.name} ({duration:.2f}s)")
            continue

        failures.append(test_file)
        print(f"[FAIL] {test_file.name} ({duration:.2f}s)")
        print(completed.stdout)
        print(completed.stderr, file=sys.stderr)

    duration = time.monotonic() - started_at
    print()
    print(
        f"Executed {len(tests)} test scripts in {duration:.2f}s; "
        f"failures={len(failures)}."
    )

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
