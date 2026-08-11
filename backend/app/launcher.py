"""Serve the packaged Concierge browser application on loopback."""

from __future__ import annotations

import os
from pathlib import Path
import webbrowser

import uvicorn

from app.api import create_app


def project_root() -> Path:
    """Return the installed artifact or repository root."""

    return Path(__file__).resolve().parents[2]


def resolve_web_root(root: Path) -> Path:
    """Return a verified, prebuilt browser bundle without invoking Node."""

    web_root = root.resolve() / "frontend" / "dist"
    index = web_root / "index.html"
    assets = web_root / "assets"
    if not index.is_file() or not assets.is_dir() or not any(assets.iterdir()):
        raise RuntimeError("packaged Concierge UI is missing index.html or built assets")
    return web_root


def launch(
    root: Path | None = None,
    *,
    data_directory: Path,
    port: int = 4173,
    open_browser: bool = True,
) -> None:
    """Serve the prebuilt UI and its API on one loopback-only port."""

    expanded_data = data_directory.expanduser()
    if not expanded_data.is_absolute():
        raise ValueError("data directory must be absolute")
    resolved_data = expanded_data.resolve(strict=False)
    os.environ["CONCIERGE_DATA_DIR"] = str(resolved_data)
    app = create_app(web_root=resolve_web_root(root or project_root()))
    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        app.router.on_startup.append(lambda: webbrowser.open(url))
    uvicorn.run(app, host="127.0.0.1", port=port)
