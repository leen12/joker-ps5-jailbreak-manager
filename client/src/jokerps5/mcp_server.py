"""MCP server exposing Joker PS5 Control as agent tools."""

from __future__ import annotations

import os
import time
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

from .client import JokerClient
from .admin import AdminManager
from .toolkit import AppProject, ProfileManager, ProjectAdapter

mcp = FastMCP("joker-ps5")


def _client() -> JokerClient:
    host = os.environ.get("PS5_HOST")
    if not host:
        raise RuntimeError("PS5_HOST environment variable is not set; point it at the console's IP")
    return JokerClient(host=host, port=int(os.environ.get("PS5_PORT", "9310")))


@mcp.tool()
def status() -> dict[str, Any]:
    """Get the virtual controller's open/closed state and basic pad info.

    Use this first to check whether open_controller is needed before pressing buttons.
    """
    return _client().pad_status()


@mcp.tool()
def open_controller(user_id: int | None = None) -> dict[str, Any]:
    """Open the virtual controller on the console.

    Must be called before press/hold/sequence or screenshot. A game only obeys
    the controller that launched it, so open this before launching the title
    you intend to control.
    """
    return _client().pad_open(user_id)


@mcp.tool()
def close_controller() -> dict[str, Any]:
    """Close the virtual controller. Call this when done to free the device."""
    return _client().pad_close()


@mcp.tool()
def press(buttons: str, ms: int = 120, screenshot_after_ms: int | None = None) -> Any:
    """Press one or more buttons together for a duration, then release.

    buttons: '+'-joined names, e.g. 'cross' or 'cross+l1'. ms: 1..10000, default 120.
    The PS button cannot be sent; use go_home instead.
    If screenshot_after_ms is set, sleeps that long after the press and returns a
    screenshot (requires the controller to be open; takes about 3 seconds).
    """
    client = _client()
    client.pad_press(buttons, ms=ms)
    if screenshot_after_ms is not None:
        time.sleep(screenshot_after_ms / 1000)
        return _screenshot_image(client)
    return {"ok": True}


@mcp.tool()
def hold(
    buttons: str = "",
    lx: int = 128,
    ly: int = 128,
    rx: int = 128,
    ry: int = 128,
    l2: int = 0,
    r2: int = 0,
) -> dict[str, Any]:
    """Hold a controller state (buttons and sticks/triggers) until changed.

    Axes are 0..255 with 128 as center. State persists until pad_set is called
    again or release_all is called. Use for continuous movement, not taps.
    """
    _client().pad_set(buttons, lx, ly, rx, ry, l2, r2)
    return {"ok": True}


@mcp.tool()
def release_all() -> dict[str, Any]:
    """Release the virtual controller to neutral (no buttons, centered sticks)."""
    _client().pad_release()
    return {"ok": True}


@mcp.tool()
def sequence(steps: list[str], screenshot_after_ms: int | None = None) -> Any:
    """Run a scripted sequence of button presses and waits, in order.

    Each step is either '<buttons>:<ms>' (press for ms) or 'w<ms>' (wait).
    Max 64 steps. If screenshot_after_ms is set, sleeps then returns a screenshot
    (requires the controller to be open; takes about 3 seconds).
    """
    client = _client()
    result = client.pad_seq(steps)
    if screenshot_after_ms is not None:
        time.sleep(screenshot_after_ms / 1000)
        return _screenshot_image(client)
    return result


def _screenshot_image(
    client: JokerClient,
    max_width: int = 1280,
    backend: str = "create",
    request_path: str | None = None,
    folder: str | None = None,
    wait_ms: int = 3000,
) -> Image:
    if backend == "latest":
        shot = client.shot_latest()
    elif backend == "grab":
        if not request_path or not folder:
            raise ValueError("grab backend requires request_path and folder")
        shot = client.shot_grab(request_path, folder, wait_ms)
    elif backend == "create":
        shot = client.shot()
    else:
        raise ValueError("backend must be create, latest, or grab")
    png = shot.to_png(max_width=max_width)
    return Image(data=png, format="png")


@mcp.tool()
def screenshot(
    max_width: int = 1280,
    backend: str = "create",
    request_path: str | None = None,
    folder: str | None = None,
    wait_ms: int = 3000,
) -> Image:
    """Capture a screenshot of the console's current screen.

    backend=create takes a new whole-screen capture using the Create shortcut.
    backend=latest downloads the newest existing gallery image without deletion.
    backend=grab asks a cooperating title for its next frame and requires its
    request_path and output folder. Returns a downscaled PNG.
    """
    return _screenshot_image(_client(), max_width, backend, request_path, folder, wait_ms)


@mcp.tool()
def list_apps() -> list[dict[str, Any]]:
    """List installed apps/games as {id, name} pairs."""
    return _client().app_list()


@mcp.tool()
def launch(title_id: str) -> dict[str, Any]:
    """Launch an app/game by title id.

    A launched game only obeys the controller that launched it, so open the
    controller before calling this if you intend to control the game.
    """
    return _client().app_launch(title_id)


@mcp.tool()
def go_home() -> dict[str, Any]:
    """Suspend the running app and show the PS5 home screen.

    This is how to reach the equivalent of pressing the PS button, which
    cannot be sent through the virtual controller.
    """
    _client().app_home()
    return {"ok": True}


@mcp.tool()
def close_app() -> dict[str, Any]:
    """Kill the currently running app/game."""
    _client().app_close()
    return {"ok": True}


@mcp.tool()
def list_files(path: str) -> list[dict[str, Any]]:
    """List directory entries ({name, type, size, mtime}) at an absolute console path."""
    return _client().fs_ls(path)


@mcp.tool()
def pull(remote_path: str, local_path: str) -> dict[str, Any]:
    """Download a file from the console to a local path."""
    data = _client().fs_get(remote_path)
    with open(local_path, "wb") as f:
        f.write(data)
    return {"ok": True, "bytes": len(data)}


@mcp.tool()
def push(local_path: str, remote_path: str) -> dict[str, Any]:
    """Upload a local file to an absolute console path.

    The console restricts writable paths to /data, /user/data, /mnt/ext1,
    /mnt/usb*, /user/av_contents, /user/app, /user/homebrew.
    """
    with open(local_path, "rb") as f:
        data = f.read()
    return _client().fs_put(remote_path, data)


@mcp.tool()
def disk_space() -> list[dict[str, Any]]:
    """List mounted filesystems with total/free bytes."""
    return _client().fs_df()


@mcp.tool()
def notify(text: str) -> dict[str, Any]:
    """Show a PS5 system notification with the given text."""
    _client().notify(text)
    return {"ok": True}


@mcp.tool()
def console_health() -> dict[str, Any]:
    """Collect a read-only health snapshot: firmware, temperatures, disk, pad, and running app."""
    return AdminManager(_client()).health()


@mcp.tool()
def audit_console() -> dict[str, Any]:
    """Audit console thermals, free-space ratios, duplicate app identities, and controller readiness."""
    return AdminManager(_client()).audit()


@mcp.tool()
def backup_console(remote_paths: list[str], local_directory: str) -> dict[str, Any]:
    """Create a local, hash-manifested backup of console files or directories."""
    from pathlib import Path

    return AdminManager(_client()).backup(remote_paths, Path(local_directory))


@mcp.tool()
def restore_console(local_directory: str) -> dict[str, Any]:
    """Restore and verify a backup created by backup_console."""
    from pathlib import Path

    return AdminManager(_client()).restore(Path(local_directory))


@mcp.tool()
def edit_console_text(remote_path: str, old: str, new: str) -> dict[str, Any]:
    """Replace one exact text occurrence, saving a timestamped rollback copy first."""
    return AdminManager(_client()).edit_text(remote_path, old, new)


@mcp.tool()
def deploy_homebrew(local_directory: str, title_id: str, shadowmount_url: str | None = None) -> dict[str, Any]:
    """Upload and hash-verify a homebrew app, then optionally register and scan it with ShadowMount."""
    from pathlib import Path

    return AdminManager(_client()).deploy_homebrew(
        Path(local_directory), title_id, shadowmount_url=shadowmount_url
    )


@mcp.tool()
def create_homebrew_project(
    destination: str,
    title_id: str,
    name: str,
    executable: str,
    icon: str | None = None,
    version: str = "01.000.000",
    category: str = "game",
) -> dict[str, Any]:
    """Create a complete, validated ShadowMount-compatible app folder with metadata and artwork."""
    from pathlib import Path

    return AppProject.create(
        Path(destination), title_id, name, Path(executable),
        icon=Path(icon) if icon else None, version=version, category=category,
    )


@mcp.tool()
def validate_homebrew_project(directory: str) -> dict[str, Any]:
    """Validate app identity, executable layout, icon dimensions, metadata, and file hashes."""
    from pathlib import Path

    return AppProject.validate(Path(directory))


@mcp.tool()
def apply_console_profile(profile: str) -> dict[str, Any]:
    """Apply exact configuration replacements as one verified, rollback-safe transaction."""
    from pathlib import Path

    return ProfileManager(_client()).apply(Path(profile))


@mcp.tool()
def create_ps5_project(
    kind: str,
    destination: str,
    title_id: str,
    name: str,
    template: str | None = None,
) -> dict[str, Any]:
    """Create a native or Vulkan project using its upstream generator and add Joker capture integration."""
    from pathlib import Path

    return ProjectAdapter.create(kind, Path(destination), title_id, name, template=template)


@mcp.tool()
def inspect_ps5_project(directory: str) -> dict[str, Any]:
    """Inspect a Joker project identity, manifests, capture paths, and upstream commands."""
    from pathlib import Path

    return ProjectAdapter.inspect(Path(directory))


@mcp.tool()
def build_ps5_project(directory: str) -> dict[str, Any]:
    """Run the upstream build command recorded for a Joker-managed project."""
    from pathlib import Path

    return ProjectAdapter.run_action(Path(directory), "build")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
