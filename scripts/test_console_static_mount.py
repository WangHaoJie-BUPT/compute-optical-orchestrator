"""Verify optional static console mounting and redirect behavior."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.api.console import mount_console


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        console_directory = Path(directory)
        (console_directory / "index.html").write_text(
            "<!doctype html><title>Console fixture</title>",
            encoding="utf-8",
        )

        app = FastAPI()
        assert mount_console(app, console_directory)

        client = TestClient(app)
        redirect = client.get(
            "/",
            follow_redirects=False,
        )
        assert redirect.status_code == 307
        assert redirect.headers["location"] == "/console/"

        response = client.get("/console/")
        assert response.status_code == 200
        assert "Console fixture" in response.text

        missing_app = FastAPI()
        assert not mount_console(
            missing_app,
            console_directory / "missing",
        )

    print("Console static mount test passed.")


if __name__ == "__main__":
    main()
