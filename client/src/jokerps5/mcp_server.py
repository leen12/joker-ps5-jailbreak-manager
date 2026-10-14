"""MCP server exposing Joker PS5 Control as agent tools."""

from __future__ import annotations

import os
import time
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

from .client import JokerClient

mcp = FastMCP("joker-ps5")


def _client() -> JokerClient:
    host = os.environ.get("PS5_HOST")
    if not host:
        raise RuntimeError("PS5_HOST environment variable is not set; point it at the console's IP")
    return JokerClient(host=host)


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


def _screenshot_image(client: JokerClient, max_width: int = 1280) -> Image:
    shot = client.shot()
    png = shot.to_png(max_width=max_width)
    return Image(data=png, format="png")


@mcp.tool()
def screenshot(max_width: int = 1280) -> Image:
    """Capture a screenshot of the console's current screen.

    Takes about 3 seconds (holds Create to trigger capture) and requires the
    virtual controller to be open first. Returns a downscaled PNG.
    """
    return _screenshot_image(_client(), max_width=max_width)


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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
