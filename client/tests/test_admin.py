import json
from pathlib import Path

import pytest

from jokerps5.admin import AdminManager


def test_backup_and_restore_roundtrip(client, server, tmp_path: Path):
    server.fs_files["/data/config/a.ini"] = b"enabled=1\n"
    server.fs_files["/data/config/sub/b.txt"] = b"hello"
    manager = AdminManager(client)
    manifest = manager.backup(["/data/config"], tmp_path / "backup")
    assert len(manifest["files"]) == 2
    server.fs_files.clear()
    result = manager.restore(tmp_path / "backup")
    assert result["restored"] == 2
    assert server.fs_files["/data/config/a.ini"] == b"enabled=1\n"


def test_restore_rejects_tampered_backup(client, server, tmp_path: Path):
    server.fs_files["/data/a.txt"] = b"original"
    manager = AdminManager(client)
    manager.backup(["/data/a.txt"], tmp_path)
    (tmp_path / "data" / "a.txt").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        manager.restore(tmp_path)


def test_edit_text_creates_backup_and_verifies(client, server):
    server.fs_files["/data/settings.ini"] = b"mode=old\n"
    result = AdminManager(client).edit_text("/data/settings.ini", "old", "new")
    assert server.fs_files["/data/settings.ini"] == b"mode=new\n"
    assert server.fs_files[result["backup"]] == b"mode=old\n"


def test_write_text_rejects_stale_content(client, server):
    server.fs_files["/data/settings.ini"] = b"mode=old\n"
    with pytest.raises(ValueError, match="changed since"):
        AdminManager(client).write_text("/data/settings.ini", "mode=new\n", "0" * 64)
    assert server.fs_files["/data/settings.ini"] == b"mode=old\n"


def test_deploy_homebrew_validates_and_uploads(client, server, tmp_path: Path):
    app = tmp_path / "app"
    (app / "sce_sys").mkdir(parents=True)
    (app / "eboot.elf").write_bytes(b"elf")
    (app / "sce_sys" / "param.json").write_text(json.dumps({"titleName": "Demo"}), encoding="utf-8")
    (app / "sce_sys" / "icon0.png").write_bytes(b"png")
    result = AdminManager(client).deploy_homebrew(app, "DEMO00001")
    assert result["files"] == 3
    assert server.fs_files["/data/homebrew/DEMO00001/eboot.elf"] == b"elf"
    assert server.fs_modes["/data/homebrew/DEMO00001/eboot.elf"] == 0o755


def test_deploy_homebrew_requires_manifest_files(client, tmp_path: Path):
    with pytest.raises(ValueError, match="exactly one"):
        AdminManager(client).deploy_homebrew(tmp_path, "DEMO00001")


def test_deploy_homebrew_accepts_eboot_bin(client, server, tmp_path: Path):
    app = tmp_path / "app"
    (app / "sce_sys").mkdir(parents=True)
    (app / "eboot.bin").write_bytes(b"self")
    (app / "sce_sys" / "param.json").write_text('{"titleName":"Demo"}', encoding="utf-8")
    (app / "sce_sys" / "icon0.png").write_bytes(b"png")
    result = AdminManager(client).deploy_homebrew(app, "DEMO00001")
    assert result["files"] == 3
    assert server.fs_modes["/data/homebrew/DEMO00001/eboot.bin"] == 0o755


def test_audit_reports_health_and_unique_apps(client, monkeypatch):
    manager = AdminManager(client)
    monkeypatch.setattr(manager, "health", lambda: {
        "system": {"temp_soc": 61, "temp_cpu": 72},
        "disk": [{"path": "/data", "total": 1000, "free": 500}],
        "pad": {"open": False, "backend": "direct-hid"},
    })
    monkeypatch.setattr(client, "app_list", lambda: [{"id": "DEMO00001", "name": "Demo"}])
    result = manager.audit()
    assert result["summary"]["warn"] == 0
    assert any(item["id"] == "app-identities" for item in result["checks"])
