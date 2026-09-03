import base64
from pathlib import Path

import pytest

from hermes_cli import web_server

pytest.importorskip("starlette.testclient")
from starlette.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    test_client = TestClient(web_server.app)
    test_client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        yield test_client
    finally:
        if previous_auth_required is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous_auth_required


def test_fs_list_sorts_and_hides_noise(client, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "b.txt").write_text("b", encoding="utf-8")
    (root / "a_dir").mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / ".git").mkdir()

    response = client.get("/api/fs/list", params={"path": str(root)})

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert [entry["name"] for entry in entries] == ["a_dir", "a.txt", "b.txt"]
    assert entries[0] == {"name": "a_dir", "path": str(root / "a_dir"), "isDirectory": True}
    assert all(entry["name"] not in {".git", "node_modules"} for entry in entries)


def test_fs_read_data_url_rejects_over_cap(client, tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, "_FS_DATA_URL_MAX_BYTES", 3)
    target = tmp_path / "image.png"
    target.write_bytes(b"1234")

    response = client.get("/api/fs/read-data-url", params={"path": str(target)})

    assert response.status_code == 413


def test_fs_download_streams_file_without_data_url_cap(client, tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, "_FS_DATA_URL_MAX_BYTES", 3)
    target = tmp_path / "report with spaces.pdf"
    target.write_bytes(b"123456")

    response = client.get("/api/fs/download", params={"path": str(target)})

    assert response.status_code == 200
    assert response.content == b"123456"
    assert response.headers["content-type"].startswith("application/pdf")
    assert "report%20with%20spaces.pdf" in response.headers["content-disposition"]


def test_fs_download_rejects_sensitive_files(client, tmp_path):
    target = tmp_path / ".env"
    target.write_text("SECRET=1", encoding="utf-8")

    response = client.get("/api/fs/download", params={"path": str(target)})

    assert response.status_code == 403


def test_fs_endpoints_require_auth(tmp_path):
    client = TestClient(web_server.app)
    target = tmp_path / "secret.txt"
    target.write_text("secret", encoding="utf-8")

    list_response = client.get("/api/fs/list", params={"path": str(tmp_path)})
    read_response = client.get("/api/fs/read-text", params={"path": str(target)})
    default_response = client.get("/api/fs/default-cwd")

    assert list_response.status_code == 401
    assert read_response.status_code == 401
    assert default_response.status_code == 401


def test_fs_list_filters_configured_hidden_dirs(client, tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "index.ts").write_text("console.log('ok')", encoding="utf-8")
    (root / "_config").mkdir()
    (root / "_config" / "secret.yaml").write_text("key: secret", encoding="utf-8")
    (root / "$RECYCLE.BIN").mkdir()

    monkeypatch.setattr(
        web_server,
        "load_config_readonly",
        lambda: {"fs": {"hidden_dirs": ["_config", "$RECYCLE.BIN"]}},
    )

    response = client.get("/api/fs/list", params={"path": str(root)})
    assert response.status_code == 200
    entries = response.json()["entries"]
    names = [e["name"] for e in entries]
    assert names == ["src"]
    assert "_config" not in names
    assert "$RECYCLE.BIN" not in names


def test_fs_direct_access_to_hidden_dirs_returns_403(client, tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    hidden_dir = root / "_config"
    hidden_dir.mkdir()
    secret_file = hidden_dir / "secret.yaml"
    secret_file.write_text("key: secret", encoding="utf-8")

    monkeypatch.setattr(
        web_server,
        "load_config_readonly",
        lambda: {"fs": {"hidden_dirs": ["_config"]}},
    )

    # Direct list of hidden directory
    list_res = client.get("/api/fs/list", params={"path": str(hidden_dir)})
    assert list_res.status_code == 403
    assert "hidden path" in list_res.json()["detail"].lower()

    # Direct read-text of file inside hidden directory
    read_res = client.get("/api/fs/read-text", params={"path": str(secret_file)})
    assert read_res.status_code == 403
    assert "hidden path" in read_res.json()["detail"].lower()



def test_fs_windows_root_normalizes_to_default_cwd(client, tmp_path, monkeypatch):
    default_dir = tmp_path / "default_workspace"
    default_dir.mkdir()
    (default_dir / "app.py").write_text("print('hello')", encoding="utf-8")

    monkeypatch.setattr(web_server, "_fs_default_cwd", lambda: str(default_dir))
    monkeypatch.setattr(web_server.os, "name", "nt")

    response = client.get("/api/fs/list", params={"path": "/"})
    assert response.status_code == 200
    entries = response.json()["entries"]
    names = [e["name"] for e in entries]
    assert "app.py" in names


def test_fs_reads_from_live_config_defaults(client, tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "src").mkdir()
    (root / "_config").mkdir()

    # When config has fs.hidden_dirs: ["_config"]
    monkeypatch.setattr(
        web_server,
        "load_config_readonly",
        lambda: {"fs": {"hidden_dirs": ["_config"]}},
    )
    res = client.get("/api/fs/list", params={"path": str(root)})
    assert res.status_code == 200
    names = [e["name"] for e in res.json()["entries"]]
    assert "_config" not in names
    assert names == ["src"]

    # Direct access returns 403
    forbidden_res = client.get("/api/fs/list", params={"path": str(root / "_config")})
    assert forbidden_res.status_code == 403


