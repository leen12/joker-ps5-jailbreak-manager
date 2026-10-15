"""Read-only environment and console readiness diagnostics."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any


CONSOLE_SERVICES = {
    "jokerd": 9310,
    "payload-manager": 8084,
    "shadowmount": 10101,
    "ftp": 2121,
    "dpiv2": 12800,
}


class EnvironmentDoctor:
    """Inspect local build prerequisites and optional live console services."""

    @staticmethod
    def run(kind: str = "all", host: str | None = None, root: Path | None = None) -> dict[str, Any]:
        if kind not in {"all", "toolkit", "native", "vulkan"}:
            raise ValueError("kind must be all, toolkit, native, or vulkan")
        checks: list[dict[str, Any]] = []

        def add(identifier: str, ok: bool, detail: str, remediation: str = "", *, required: bool = True) -> None:
            checks.append({
                "id": identifier,
                "status": "ok" if ok else ("fail" if required else "warn"),
                "required": required,
                "detail": detail,
                "remediation": "" if ok else remediation,
            })

        add("python", sys.version_info >= (3, 10), platform.python_version(), "Install Python 3.10 or newer")
        for name, remediation in (
            ("git", "Install Git and add it to PATH"),
            ("uv", "Install uv from https://docs.astral.sh/uv/"),
        ):
            found = shutil.which(name)
            add(name, bool(found), found or "not found", remediation)

        project_root = (root or Path(__file__).resolve().parents[3]).resolve()
        sdk_candidates = [
            Path(os.environ["PS5_PAYLOAD_SDK"]) if os.environ.get("PS5_PAYLOAD_SDK") else None,
            project_root / ".toolchain" / "sdk" / "ps5-payload-sdk",
        ]
        sdk = next((path for path in sdk_candidates if path and path.is_dir()), None)
        sdk_required = kind in {"all", "toolkit"}
        add(
            "payload-sdk",
            sdk is not None,
            str(sdk) if sdk else "not found",
            "Set PS5_PAYLOAD_SDK or run the repository payload build bootstrap",
            required=sdk_required,
        )
        clang_candidates = [shutil.which("clang"), str(project_root / ".toolchain" / "llvm" / "bin" / "clang.exe")]
        clang = next((Path(value) for value in clang_candidates if value and Path(value).is_file()), None)
        add(
            "clang",
            clang is not None,
            str(clang) if clang else "not found",
            "Install clang or prepare the repository-local LLVM toolchain",
            required=sdk_required,
        )

        if platform.system() == "Windows" and kind in {"all", "native", "vulkan"}:
            wsl = shutil.which("wsl") or shutil.which("wsl.exe")
            add("wsl", bool(wsl), wsl or "not found", "Enable Windows Subsystem for Linux")
            distributions: list[str] = []
            if wsl:
                completed = subprocess.run([wsl, "--list", "--quiet"], capture_output=True, check=False)
                output = EnvironmentDoctor._decode_windows_output(completed.stdout + completed.stderr)
                if completed.returncode == 0 and "no installed distributions" not in output.lower():
                    distributions = [line.strip().strip("\x00") for line in output.splitlines() if line.strip().strip("\x00")]
            add(
                "wsl-distribution",
                bool(distributions),
                ", ".join(distributions) if distributions else "none installed",
                "Install and initialize a Linux distribution with: wsl --install -d Ubuntu",
            )
            if distributions:
                probe = subprocess.run(
                    [wsl, "--exec", "sh", "-lc", "command -v bash git cmake ninja python3"],
                    capture_output=True,
                    check=False,
                )
                tools = EnvironmentDoctor._decode_windows_output(probe.stdout)
                add(
                    "wsl-build-tools",
                    probe.returncode == 0,
                    tools or "tool probe failed",
                    "Inside WSL install bash, git, cmake, ninja-build, build-essential and python3",
                )
        elif kind in {"all", "native", "vulkan"}:
            for command in ("bash", "cmake", "ninja", "python3"):
                found = shutil.which(command)
                add(f"build:{command}", bool(found), found or "not found", f"Install {command}")

        if host:
            for service, port in CONSOLE_SERVICES.items():
                reachable, detail = EnvironmentDoctor._probe(host, port)
                add(
                    f"console:{service}",
                    reachable,
                    detail,
                    f"Start {service} on the console (TCP {port})",
                    required=service == "jokerd",
                )

        summary = {state: sum(1 for check in checks if check["status"] == state) for state in ("ok", "warn", "fail")}
        return {
            "ok": summary["fail"] == 0,
            "kind": kind,
            "platform": platform.platform(),
            "root": str(project_root),
            "checks": checks,
            "summary": summary,
        }

    @staticmethod
    def _decode_windows_output(raw: bytes) -> str:
        if not raw:
            return ""
        if raw.count(b"\x00") > len(raw) // 4:
            return raw.decode("utf-16le", "replace").lstrip("\ufeff").strip()
        return raw.decode(errors="replace").strip()

    @staticmethod
    def _probe(host: str, port: int) -> tuple[bool, str]:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True, f"{host}:{port} reachable"
        except OSError as exc:
            return False, f"{host}:{port} unavailable ({exc})"
