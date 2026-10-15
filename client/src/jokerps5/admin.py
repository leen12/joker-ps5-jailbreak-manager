"""High-level, reversible PS5 administration workflows."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .client import JokerClient


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _join_remote(parent: str, name: str) -> str:
    return parent.rstrip("/") + "/" + name


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int
    sha256: str


class AdminManager:
    """Safe administration built on top of the small jokerd wire protocol."""

    def __init__(self, client: JokerClient) -> None:
        self.client = client

    def walk(self, remote_path: str) -> list[str]:
        """Return all regular files below a remote path, in stable order."""
        stat = self.client.fs_stat(remote_path)
        if stat["type"] != "d":
            return [remote_path]
        files: list[str] = []
        for entry in sorted(self.client.fs_ls(remote_path), key=lambda item: item["name"]):
            child = _join_remote(remote_path, entry["name"])
            if entry["type"] == "d":
                files.extend(self.walk(child))
            elif entry["type"] == "f":
                files.append(child)
        return files

    def backup(self, remote_paths: Iterable[str], destination: Path) -> dict[str, Any]:
        """Download files and write a hash-verified restore manifest."""
        destination.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        for root in remote_paths:
            for remote in self.walk(root):
                data = self.client.fs_get(remote)
                local = destination / remote.lstrip("/")
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(data)
                records.append({"path": remote, "size": len(data), "sha256": _sha256(data)})
        manifest = {
            "format": "jokerps5-backup-v1",
            "created_unix": int(time.time()),
            "files": records,
        }
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def restore(self, source: Path, *, verify: bool = True) -> dict[str, Any]:
        """Restore a Joker backup after validating every local hash."""
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != "jokerps5-backup-v1":
            raise ValueError("unsupported backup manifest")
        restored = 0
        for item in manifest["files"]:
            local = source / item["path"].lstrip("/")
            data = local.read_bytes()
            if len(data) != item["size"] or _sha256(data) != item["sha256"]:
                raise ValueError(f"backup hash mismatch: {local}")
            self.client.fs_mkdir(str(Path(item["path"]).parent).replace("\\", "/"))
            self.client.fs_put(item["path"], data)
            if verify and _sha256(self.client.fs_get(item["path"])) != item["sha256"]:
                raise IOError(f"remote verification failed: {item['path']}")
            restored += 1
        return {"ok": True, "restored": restored}

    def edit_text(self, remote_path: str, old: str, new: str) -> dict[str, Any]:
        """Replace text once, preserving a timestamped remote backup."""
        original = self.client.fs_get(remote_path)
        text = original.decode("utf-8")
        matches = text.count(old)
        if matches != 1:
            raise ValueError(f"expected exactly one match, found {matches}")
        changed = text.replace(old, new, 1).encode("utf-8")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        backup_path = f"{remote_path}.joker-backup-{stamp}"
        self.client.fs_put(backup_path, original)
        self.client.fs_put(remote_path, changed)
        actual = self.client.fs_get(remote_path)
        if actual != changed:
            self.client.fs_put(remote_path, original)
            raise IOError("verification failed; original restored")
        return {"ok": True, "backup": backup_path, "bytes": len(changed), "sha256": _sha256(changed)}

    def write_text(self, remote_path: str, text: str, expected_sha256: str) -> dict[str, Any]:
        """Write UTF-8 text with optimistic concurrency, backup, and readback verification."""
        original = self.client.fs_get(remote_path)
        actual_digest = _sha256(original)
        if actual_digest != expected_sha256:
            raise ValueError("remote file changed since it was opened")
        original.decode("utf-8")
        changed = text.encode("utf-8")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        backup_path = f"{remote_path}.joker-backup-{stamp}"
        self.client.fs_put(backup_path, original)
        self.client.fs_put(remote_path, changed)
        if self.client.fs_get(remote_path) != changed:
            self.client.fs_put(remote_path, original)
            raise IOError("verification failed; original restored")
        return {"ok": True, "backup": backup_path, "bytes": len(changed), "sha256": _sha256(changed)}

    def deploy_homebrew(
        self,
        local_dir: Path,
        title_id: str,
        *,
        shadowmount_url: str | None = None,
        verify: bool = True,
    ) -> dict[str, Any]:
        """Deploy a loose homebrew tree and optionally register it with ShadowMount."""
        local_dir = local_dir.resolve()
        if not local_dir.is_dir():
            raise ValueError(f"not a directory: {local_dir}")
        if not title_id or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in title_id):
            raise ValueError("title_id must use uppercase letters, digits, underscore, or hyphen")
        executables = [path for path in (local_dir / "eboot.bin", local_dir / "eboot.elf") if path.is_file()]
        if len(executables) != 1:
            raise ValueError("exactly one of eboot.bin or eboot.elf is required")
        required = [local_dir / "sce_sys" / "param.json", local_dir / "sce_sys" / "icon0.png"]
        missing = [str(path.relative_to(local_dir)) for path in required if not path.is_file()]
        if missing:
            raise ValueError("missing required homebrew files: " + ", ".join(missing))

        remote_root = f"/data/homebrew/{title_id}"
        uploaded: list[RemoteFile] = []
        for local in sorted(path for path in local_dir.rglob("*") if path.is_file()):
            relative = local.relative_to(local_dir).as_posix()
            remote = _join_remote(remote_root, relative)
            self.client.fs_mkdir(remote.rsplit("/", 1)[0])
            data = local.read_bytes()
            digest = _sha256(data)
            executable = local.suffix.lower() in {".elf", ".bin", ".prx", ".sprx"}
            self.client.fs_put(remote, data, mode=0o755 if executable else 0o644)
            if verify and _sha256(self.client.fs_get(remote)) != digest:
                raise IOError(f"remote verification failed: {remote}")
            if executable:
                remote_mode = int(self.client.fs_stat(remote)["mode"], 8)
                if remote_mode & 0o111 == 0:
                    raise IOError(f"remote executable bit missing: {remote}")
            uploaded.append(RemoteFile(remote, len(data), digest))

        shadowmount: dict[str, Any] | None = None
        if shadowmount_url:
            base = shadowmount_url.rstrip("/")
            self._post_json(base + "/api/v1/manual/add", {"path": remote_root})
            shadowmount = self._post_json(base + "/api/v1/scan", {"reset_attempts": True})
        return {
            "ok": True,
            "remote_root": remote_root,
            "files": len(uploaded),
            "bytes": sum(item.size for item in uploaded),
            "shadowmount": shadowmount,
        }

    def health(self) -> dict[str, Any]:
        """Collect a read-only console health snapshot."""
        return {
            "hello": self.client.hello(),
            "system": self.client.sys_info(),
            "disk": self.client.fs_df(),
            "pad": self.client.pad_status(),
            "running_app": self.client.app_running(),
        }

    def audit(self) -> dict[str, Any]:
        """Evaluate health, storage, app identity, and controller readiness."""
        health = self.health()
        checks: list[dict[str, Any]] = []
        for sensor in ("temp_soc", "temp_cpu"):
            value = int(health["system"][sensor])
            checks.append({
                "id": sensor,
                "status": "warn" if value >= 85 else "ok",
                "value": value,
                "message": f"{sensor.removeprefix('temp_').upper()} temperature is {value} C",
            })
        for disk in health["disk"]:
            total = int(disk["total"])
            free = int(disk["free"])
            ratio = free / total if total else 0
            checks.append({
                "id": f"disk:{disk['path']}",
                "status": "warn" if ratio < 0.1 else "ok",
                "value": round(ratio * 100, 1),
                "message": f"{disk['path']} has {ratio:.1%} free",
            })
        apps = self.client.app_list()
        ids = [str(app.get("id", "")) for app in apps]
        duplicates = sorted({title_id for title_id in ids if ids.count(title_id) > 1})
        checks.append({
            "id": "app-identities",
            "status": "warn" if duplicates else "ok",
            "value": len(apps),
            "message": "duplicate title IDs: " + ", ".join(duplicates) if duplicates else f"{len(apps)} unique app IDs",
        })
        checks.append({
            "id": "controller",
            "status": "ok" if health["pad"].get("open") else "info",
            "value": health["pad"].get("backend"),
            "message": "virtual controller open" if health["pad"].get("open") else "virtual controller is closed",
        })
        return {
            "ok": not any(item["status"] == "warn" for item in checks),
            "checks": checks,
            "summary": {
                state: sum(1 for item in checks if item["status"] == state)
                for state in ("ok", "info", "warn")
            },
        }

    @staticmethod
    def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
        return json.loads(raw) if raw else {"ok": True}
