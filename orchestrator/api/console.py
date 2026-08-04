"""Serve the optional production build of the demo console."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles


def mount_console(
    app: FastAPI,
    console_directory: str | Path,
) -> bool:
    """Mount a built console when its index is present."""

    resolved = Path(console_directory)
    index_file = resolved / "index.html"

    if not index_file.is_file():
        return False

    @app.get(
        "/",
        include_in_schema=False,
        response_class=RedirectResponse,
    )
    def console_redirect() -> RedirectResponse:
        return RedirectResponse(
            url="/console/",
            status_code=307,
        )

    app.mount(
        "/console",
        StaticFiles(
            directory=resolved,
            html=True,
        ),
        name="console",
    )
    return True
