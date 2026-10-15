import json
from pathlib import Path

import pytest

from jokerps5.toolkit import AppProject, ProfileManager, ProjectAdapter


def test_create_and_validate_app(tmp_path: Path):
    executable = tmp_path / "demo.elf"
    executable.write_bytes(b"ELF payload")
    result = AppProject.create(tmp_path / "out", "JOKR00001", "Joker Demo", executable)
    assert result["ok"] is True
    root = tmp_path / "out" / "JOKR00001"
    manifest = json.loads((root / "sce_sys" / "param.json").read_text(encoding="utf-8"))
    assert manifest["titleId"] == "JOKR00001"
    assert manifest["contentId"] == "IV0000-JOKR00001_00-JOKERDEMO0000000"
    assert (root / "sce_sys" / "icon0.png").is_file()
    assert len(result["files"]) == 3


def test_validate_app_reports_bad_layout(tmp_path: Path):
    root = tmp_path / "BAD"
    root.mkdir()
    result = AppProject.validate(root)
    assert result["ok"] is False
    assert "sce_sys/param.json is missing" in result["errors"]


def test_create_app_rejects_invalid_identity(tmp_path: Path):
    executable = tmp_path / "demo.elf"
    executable.write_bytes(b"x")
    with pytest.raises(ValueError, match="four uppercase"):
        AppProject.create(tmp_path / "out", "bad", "Demo", executable)


def test_profile_applies_all_changes_with_backups(client, server, tmp_path: Path):
    server.fs_files["/data/a.ini"] = b"quality=old\n"
    server.fs_files["/data/b.ini"] = b"enabled=0\n"
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"name": "demo", "changes": [
        {"path": "/data/a.ini", "old": "quality=old", "new": "quality=new"},
        {"path": "/data/b.ini", "old": "enabled=0", "new": "enabled=1"},
    ]}), encoding="utf-8")
    result = ProfileManager(client).apply(profile)
    assert result["changed"] == ["/data/a.ini", "/data/b.ini"]
    assert server.fs_files["/data/a.ini"] == b"quality=new\n"
    assert server.fs_files[result["backups"]["/data/a.ini"]] == b"quality=old\n"


def test_profile_preflight_prevents_partial_change(client, server, tmp_path: Path):
    server.fs_files["/data/a.ini"] = b"quality=old\n"
    server.fs_files["/data/b.ini"] = b"enabled=0\n"
    profile = tmp_path / "bad.json"
    profile.write_text(json.dumps({"changes": [
        {"path": "/data/a.ini", "old": "quality=old", "new": "quality=new"},
        {"path": "/data/b.ini", "old": "missing", "new": "enabled=1"},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="expected exactly one"):
        ProfileManager(client).apply(profile)
    assert server.fs_files["/data/a.ini"] == b"quality=old\n"


def _template_manifest(title_id: str = "PPSA99999") -> dict:
    return {
        "titleId": title_id,
        "conceptId": title_id[-5:],
        "contentId": f"UP9000-{title_id}_00-HELLOWORLD000001",
        "contentVersion": "01.000.000",
        "localizedParameters": {"en-US": {"titleName": "Template"}},
    }


def test_native_project_adapter(tmp_path: Path):
    template = tmp_path / "native-template"
    (template / "sce_sys").mkdir(parents=True)
    (template / "sce_sys" / "param.json").write_text(json.dumps(_template_manifest()), encoding="utf-8")
    (template / "build.ps1").write_text("exit 0\n", encoding="utf-8")
    capture = Path(__file__).resolve().parents[2] / "integrations" / "joker_capture.h"
    result = ProjectAdapter.create(
        "native", tmp_path / "output", "JOKR10001", "Native Demo", template=str(template), capture_header=capture
    )
    assert result["ok"] is True
    assert result["kind"] == "native"
    assert (tmp_path / "output" / "integrations" / "joker_capture.h").is_file()
    manifest = json.loads((tmp_path / "output" / "sce_sys" / "param.json").read_text(encoding="utf-8"))
    assert manifest["titleId"] == "JOKR10001"
    assert manifest["localizedParameters"]["en-US"]["titleName"] == "Native Demo"


def test_vulkan_project_adapter_uses_upstream_generator(tmp_path: Path):
    template = tmp_path / "vulkan-template"
    generator = template / "ps5" / "tools" / "new-title.py"
    generator.parent.mkdir(parents=True)
    generator.write_text(
        "import json,pathlib,sys\n"
        "out=pathlib.Path(sys.argv[1]); title=sys.argv[sys.argv.index('--title-id')+1]\n"
        "p=out/'ps5'/'sce_sys'; p.mkdir(parents=True)\n"
        "(p/'param.json').write_text(json.dumps({'titleId':title,'contentId':'UP9000-'+title+'_00-TEMPLATE00000000','contentVersion':'01.000.000','localizedParameters':{'en-US':{'titleName':'Template'}}}))\n",
        encoding="utf-8",
    )
    capture = Path(__file__).resolve().parents[2] / "integrations" / "joker_capture.h"
    result = ProjectAdapter.create(
        "vulkan", tmp_path / "vk-output", "PPSA10002", "Vulkan Demo", template=str(template), capture_header=capture
    )
    assert result["ok"] is True
    assert result["commands"]["build"] == ["bash", "ps5/tools/build.sh"]


def test_vulkan_adapter_rejects_non_ppsa_id(tmp_path: Path):
    with pytest.raises(ValueError, match="requires title_id PPSA"):
        ProjectAdapter.create("vulkan", tmp_path / "out", "JOKR10002", "Demo")


def test_project_find_app_uses_dist_title_folder(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".jokerps5.json").write_text(json.dumps({
        "format": "jokerps5-project-v1", "kind": "native", "title_id": "JOKR10003", "name": "Demo",
        "capture": {"request": "/data/r", "folder": "/data/f", "header": "integrations/joker_capture.h"},
    }), encoding="utf-8")
    source_manifest = root / "sce_sys" / "param.json"
    source_manifest.parent.mkdir()
    source_manifest.write_text(json.dumps(_template_manifest("JOKR10003")), encoding="utf-8")
    app = root / "dist" / "JOKR10003"
    (app / "sce_sys").mkdir(parents=True)
    (app / "sce_sys" / "param.json").write_text(json.dumps(_template_manifest("JOKR10003")), encoding="utf-8")
    assert ProjectAdapter.find_app(root) == app
