"""Application creation, validation, and transactional console profiles."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .client import JokerClient


TITLE_ID_RE = re.compile(r"^[A-Z]{4}[0-9]{5}$")
VERSION_RE = re.compile(r"^[0-9]{2}\.[0-9]{3}\.[0-9]{3}$")
PROJECT_TEMPLATES = {
    "native": "https://github.com/blackbearreloaded/ps5-native-app-boilerplate.git",
    "vulkan": "https://github.com/hotshotz79/PS5_VulkanTemplate.git",
}


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


class AppProject:
    """Create and validate ShadowMount-compatible native app folders."""

    @staticmethod
    def create(
        destination: Path,
        title_id: str,
        name: str,
        executable: Path,
        *,
        icon: Path | None = None,
        version: str = "01.000.000",
        category: str = "game",
    ) -> dict[str, Any]:
        AppProject._validate_identity(title_id, name, version, category)
        executable = executable.resolve()
        if not executable.is_file():
            raise ValueError(f"executable not found: {executable}")
        root = destination.resolve() / title_id
        if root.exists() and any(root.iterdir()):
            raise ValueError(f"destination already contains files: {root}")
        sce_sys = root / "sce_sys"
        sce_sys.mkdir(parents=True, exist_ok=True)
        target_name = "eboot.bin" if executable.suffix.lower() == ".bin" else "eboot.elf"
        shutil.copyfile(executable, root / target_name)
        suffix = re.sub(r"[^A-Z0-9]", "", name.upper())[:16].ljust(16, "0")
        manifest: dict[str, Any] = {
            "applicationCategoryType": 0 if category == "game" else 65536,
            "contentBadgeType": 1 if category == "game" else 2,
            "contentId": f"IV0000-{title_id}_00-{suffix}",
            "contentVersion": version,
            "localizedParameters": {"en-US": {"titleName": name}},
            "masterVersion": version[:5],
            "titleId": title_id,
            "titleName": name,
        }
        (sce_sys / "param.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if icon:
            shutil.copyfile(icon.resolve(), sce_sys / "icon0.png")
        else:
            AppProject._default_icon(sce_sys / "icon0.png", name)
        return AppProject.validate(root)

    @staticmethod
    def validate(root: Path) -> dict[str, Any]:
        root = root.resolve()
        errors: list[str] = []
        warnings: list[str] = []
        param_path = root / "sce_sys" / "param.json"
        icon_path = root / "sce_sys" / "icon0.png"
        executables = [path for path in (root / "eboot.bin", root / "eboot.elf") if path.is_file()]
        if len(executables) != 1:
            errors.append("exactly one of eboot.bin or eboot.elf is required")
        try:
            param = json.loads(param_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            param = {}
            errors.append("sce_sys/param.json is missing")
        except json.JSONDecodeError as exc:
            param = {}
            errors.append(f"sce_sys/param.json is invalid JSON: {exc}")
        title_id = param.get("titleId", "")
        if not TITLE_ID_RE.fullmatch(title_id):
            errors.append("titleId must be four uppercase letters followed by five digits")
        if root.name != title_id:
            warnings.append("folder name does not match titleId")
        if not str(param.get("titleName", "")).strip():
            errors.append("titleName is required")
        if not VERSION_RE.fullmatch(str(param.get("contentVersion", ""))):
            errors.append("contentVersion must use NN.NNN.NNN")
        content_id = str(param.get("contentId", ""))
        if title_id and title_id not in content_id:
            errors.append("contentId must contain titleId")
        try:
            with Image.open(icon_path) as image:
                if image.format != "PNG":
                    errors.append("sce_sys/icon0.png must be PNG")
                if image.size != (512, 512):
                    errors.append("sce_sys/icon0.png must be 512x512")
        except FileNotFoundError:
            errors.append("sce_sys/icon0.png is missing")
        except OSError as exc:
            errors.append(f"sce_sys/icon0.png is invalid: {exc}")
        files = [path for path in sorted(root.rglob("*")) if path.is_file()]
        return {
            "ok": not errors,
            "root": str(root),
            "title_id": title_id,
            "errors": errors,
            "warnings": warnings,
            "files": [{"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": _digest(path)} for path in files],
        }

    @staticmethod
    def _validate_identity(title_id: str, name: str, version: str, category: str) -> None:
        if not TITLE_ID_RE.fullmatch(title_id):
            raise ValueError("title_id must be four uppercase letters followed by five digits")
        if not name.strip():
            raise ValueError("name must not be empty")
        if not VERSION_RE.fullmatch(version):
            raise ValueError("version must use NN.NNN.NNN")
        if category not in {"game", "media"}:
            raise ValueError("category must be game or media")

    @staticmethod
    def _default_icon(path: Path, name: str) -> None:
        image = Image.new("RGB", (512, 512), (8, 10, 18))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((34, 34, 478, 478), radius=72, fill=(18, 28, 54), outline=(45, 162, 255), width=8)
        initials = "".join(word[0] for word in name.split() if word)[:3].upper() or "APP"
        box = draw.textbbox((0, 0), initials)
        draw.text(((512 - (box[2] - box[0])) / 2, (512 - (box[3] - box[1])) / 2), initials, fill=(236, 244, 255))
        image.save(path, format="PNG", optimize=True)


class ProfileManager:
    """Apply exact text configuration changes as one rollback-safe transaction."""

    def __init__(self, client: JokerClient) -> None:
        self.client = client

    def apply(self, profile_path: Path) -> dict[str, Any]:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        changes = profile.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("profile must contain a non-empty changes list")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        originals: dict[str, bytes] = {}
        prepared: list[tuple[str, bytes]] = []
        for index, change in enumerate(changes):
            if not isinstance(change, dict):
                raise ValueError(f"change {index} must be an object")
            path = str(change.get("path", ""))
            old = str(change.get("old", ""))
            new = str(change.get("new", ""))
            if not path.startswith("/") or not old:
                raise ValueError(f"change {index} requires absolute path and non-empty old text")
            original = self.client.fs_get(path)
            text = original.decode("utf-8")
            count = text.count(old)
            if count != 1:
                raise ValueError(f"{path}: expected exactly one match, found {count}")
            originals[path] = original
            prepared.append((path, text.replace(old, new, 1).encode("utf-8")))
        backup_paths: dict[str, str] = {}
        applied: list[str] = []
        try:
            for path, original in originals.items():
                backup = f"{path}.joker-profile-{stamp}"
                self.client.fs_put(backup, original)
                backup_paths[path] = backup
            for path, changed in prepared:
                self.client.fs_put(path, changed)
                if self.client.fs_get(path) != changed:
                    raise IOError(f"verification failed: {path}")
                applied.append(path)
        except Exception:
            for path in reversed(applied):
                self.client.fs_put(path, originals[path])
            raise
        return {"ok": True, "name": profile.get("name", profile_path.stem), "changed": applied, "backups": backup_paths}


class ProjectAdapter:
    """Create and operate upstream-native PS5 application projects."""

    @staticmethod
    def create(
        kind: str,
        destination: Path,
        title_id: str,
        name: str,
        *,
        template: str | None = None,
        capture_header: Path | None = None,
    ) -> dict[str, Any]:
        AppProject._validate_identity(title_id, name, "01.000.000", "game")
        if kind not in PROJECT_TEMPLATES:
            raise ValueError("kind must be native or vulkan")
        if kind == "vulkan" and not re.fullmatch(r"PPSA[0-9]{5}", title_id):
            raise ValueError("Vulkan template requires title_id PPSA followed by five digits")
        destination = destination.resolve()
        if destination.exists():
            raise ValueError(f"destination already exists: {destination}")
        source = template or PROJECT_TEMPLATES[kind]
        if kind == "native":
            ProjectAdapter._materialize(source, destination)
        else:
            with tempfile.TemporaryDirectory(prefix="jokerps5-vulkan-") as temporary:
                checkout = Path(temporary) / "template"
                ProjectAdapter._materialize(source, checkout)
                generator = checkout / "ps5" / "tools" / "new-title.py"
                if not generator.is_file():
                    raise ValueError(f"Vulkan template generator missing: {generator}")
                ProjectAdapter._run(
                    [sys.executable, str(generator), str(destination), "--title-id", title_id, "--name", name],
                    cwd=checkout,
                    capture=True,
                )

        manifests = ProjectAdapter._identity_manifests(destination)
        if not manifests:
            raise ValueError("generated project contains no PS5 param.json")
        for manifest in manifests:
            ProjectAdapter._rewrite_manifest(manifest, title_id, name)

        integration = destination / "integrations"
        integration.mkdir(parents=True, exist_ok=True)
        if capture_header is None:
            capture_header = Path(__file__).resolve().parents[3] / "integrations" / "joker_capture.h"
        if not capture_header.is_file():
            raise ValueError(f"capture integration header missing: {capture_header}")
        shutil.copyfile(capture_header, integration / "joker_capture.h")

        metadata = {
            "format": "jokerps5-project-v1",
            "kind": kind,
            "title_id": title_id,
            "name": name,
            "template": source,
            "capture": {
                "request": f"/data/homebrew/{title_id}/joker-shot-request",
                "folder": f"/data/homebrew/{title_id}/joker-shots",
                "header": "integrations/joker_capture.h",
            },
        }
        (destination / ".jokerps5.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        return ProjectAdapter.inspect(destination)

    @staticmethod
    def inspect(directory: Path) -> dict[str, Any]:
        directory = directory.resolve()
        metadata_path = directory / ".jokerps5.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("format") != "jokerps5-project-v1":
            raise ValueError("unsupported Joker project metadata")
        manifests = ProjectAdapter._identity_manifests(directory)
        issues: list[str] = []
        for path in manifests:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("titleId") != metadata["title_id"]:
                issues.append(f"{path.relative_to(directory)} titleId mismatch")
        commands = ProjectAdapter.commands(directory, metadata["kind"])
        return {
            "ok": bool(manifests) and not issues,
            "root": str(directory),
            "kind": metadata["kind"],
            "title_id": metadata["title_id"],
            "name": metadata["name"],
            "manifests": [str(path.relative_to(directory)) for path in manifests],
            "capture": metadata["capture"],
            "commands": commands,
            "issues": issues,
        }

    @staticmethod
    def commands(directory: Path, kind: str) -> dict[str, list[str]]:
        if kind == "native":
            build = ["powershell", "-File", "build.ps1"] if (directory / "build.ps1").is_file() else ["make"]
            doctor = ["powershell", "-File", "tools/doctor.ps1"] if (directory / "tools" / "doctor.ps1").is_file() else ["make", "doctor"]
        elif kind == "vulkan":
            build = ["bash", "ps5/tools/build.sh"]
            doctor = ["bash", "ps5/tools/bootstrap.sh"]
        else:
            raise ValueError("kind must be native or vulkan")
        return {"doctor": doctor, "build": build}

    @staticmethod
    def run_action(directory: Path, action: str) -> dict[str, Any]:
        project = ProjectAdapter.inspect(directory)
        if action not in {"doctor", "build"}:
            raise ValueError("action must be doctor or build")
        command = project["commands"][action]
        completed = ProjectAdapter._run(command, cwd=directory)
        return {"ok": True, "action": action, "command": command, "returncode": completed.returncode}

    @staticmethod
    def find_app(directory: Path) -> Path:
        project = ProjectAdapter.inspect(directory)
        title_id = project["title_id"]
        candidates = [directory / "dist" / title_id, directory / title_id, directory / "build" / "app" / title_id]
        for candidate in candidates:
            if candidate.is_dir() and (candidate / "sce_sys" / "param.json").is_file():
                return candidate
        matches = [path.parent.parent for path in directory.rglob("sce_sys/param.json") if path.parent.parent.name == title_id]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(f"built app folder not found for {title_id}; run project-build first")

    @staticmethod
    def _materialize(source: str, destination: Path) -> None:
        local = Path(source).expanduser()
        if local.is_dir():
            shutil.copytree(local, destination, ignore=shutil.ignore_patterns(".git", "build", "dist", ".deps"))
            return
        ProjectAdapter._run(["git", "clone", "--depth", "1", source, str(destination)], capture=True)

    @staticmethod
    def _identity_manifests(directory: Path) -> list[Path]:
        manifests: list[Path] = []
        for path in sorted(directory.rglob("param.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if "titleId" in value and "contentId" in value:
                manifests.append(path)
        return manifests

    @staticmethod
    def _rewrite_manifest(path: Path, title_id: str, name: str) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        old_title_id = str(value.get("titleId", ""))
        value["titleId"] = title_id
        value["conceptId"] = title_id[-5:]
        content_id = str(value.get("contentId", ""))
        suffix = re.sub(r"[^A-Z0-9]", "", name.upper())[:16].ljust(16, "0")
        prefix = content_id.split("-", 1)[0] if "-" in content_id else "UP9000"
        value["contentId"] = f"{prefix}-{title_id}_00-{suffix}"
        value["titleName"] = name
        localized = value.setdefault("localizedParameters", {})
        if isinstance(localized, dict):
            language = "en-US" if "en-US" in localized or "defaultLanguage" in localized else next(
                (key for key, item in localized.items() if isinstance(item, dict)), "en-US"
            )
            localized.setdefault(language, {})["titleName"] = name
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        if old_title_id and old_title_id != title_id:
            for text_path in path.parents[1].rglob("*"):
                if not text_path.is_file() or text_path == path or text_path.stat().st_size > 2 * 1024 * 1024:
                    continue
                try:
                    text = text_path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if old_title_id in text:
                    text_path.write_text(text.replace(old_title_id, title_id), encoding="utf-8")

    @staticmethod
    def _run(
        command: list[str], cwd: Path | None = None, *, capture: bool = False
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=capture)
        except FileNotFoundError as exc:
            raise RuntimeError(f"required command not found: {command[0]}") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"command failed ({exc.returncode}): {' '.join(command)}") from exc
