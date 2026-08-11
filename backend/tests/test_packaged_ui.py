from importlib import import_module
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_release_package_contains_a_prebuilt_browser_bundle():
    web_root = ROOT / "frontend" / "dist"

    assert (web_root / "index.html").is_file()
    assets = [path for path in (web_root / "assets").iterdir() if path.is_file()]
    assert assets

    manifest = (ROOT / "manifest.yaml").read_text(encoding="utf-8")
    assert "backend/app/launcher.py" in manifest
    assert "scripts/launch.py" in manifest
    assert "frontend/dist/index.html" in manifest
    for asset in assets:
        assert asset.relative_to(ROOT).as_posix() in manifest


def test_create_app_serves_the_bundle_without_shadowing_api_routes(tmp_path, monkeypatch):
    api = import_module("app.api")

    class Library:
        pass

    monkeypatch.setattr(api, "open_default_library", lambda: Library())
    web_root = tmp_path / "dist"
    assets = web_root / "assets"
    assets.mkdir(parents=True)
    (web_root / "index.html").write_text("<main>Concierge</main>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('concierge')", encoding="utf-8")

    client = TestClient(api.create_app(web_root=web_root))

    assert client.get("/").text == "<main>Concierge</main>"
    assert client.get("/assets/app.js").text == "console.log('concierge')"
    assert client.get("/api/health").json() == {"status": "ok"}


def test_launcher_uses_the_packaged_bundle_without_node_or_npm(tmp_path, monkeypatch):
    launcher = import_module("app.launcher")
    web_root = tmp_path / "frontend" / "dist"
    (web_root / "assets").mkdir(parents=True)
    (web_root / "index.html").write_text("built", encoding="utf-8")
    (web_root / "assets" / "app.js").write_text("built", encoding="utf-8")
    app = FastAPI(title="Concierge")
    calls = []

    monkeypatch.setattr(launcher, "create_app", lambda *, web_root: app)
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))

    launcher.launch(tmp_path, data_directory=tmp_path / "data", port=8123, open_browser=False)

    assert calls == [(app, {"host": "127.0.0.1", "port": 8123})]
    assert launcher.resolve_web_root(tmp_path) == web_root
    source = (ROOT / "backend" / "app" / "launcher.py").read_text(encoding="utf-8")
    assert "npm" not in source.lower()
    assert "node_modules" not in source


def test_launcher_rejects_a_relative_data_directory(tmp_path):
    launcher = import_module("app.launcher")

    with pytest.raises(ValueError, match="data directory must be absolute"):
        launcher.launch(tmp_path, data_directory=Path("relative-data"), open_browser=False)


def test_onboarding_requires_starting_the_ui_and_pointing_the_user_to_it():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Start the Concierge UI" in skill
    assert "ui.launch_command" in skill
    assert "ui.readiness_url" in skill
    assert "point the user" in skill.lower()
    assert "prebuilt" in readme.lower()
    assert "does not require Node" in readme
