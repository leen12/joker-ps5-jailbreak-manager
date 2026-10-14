"""Command-line interface for jokerps5."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .client import JokerClient, JokerError
from .buttons import format_mask, parse_buttons


def _translate_seq_step(step: str, default_ms: int) -> str:
    """Translate a friendly seq step into the wire format.

    'cross' -> '<mask>:<default_ms>', 'cross:300' -> '<mask>:300',
    'wait:1000' -> 'w1000', 'down' -> '<mask>:<default_ms>'.
    """
    if step.startswith("wait:"):
        return f"w{int(step.split(':', 1)[1])}"
    if ":" in step:
        name, ms = step.split(":", 1)
        return f"{format_mask(parse_buttons(name))}:{int(ms)}"
    return f"{format_mask(parse_buttons(step))}:{default_ms}"


def _print_json(data: Any) -> None:
    print(json.dumps(data))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jokerps5")
    parser.add_argument("--host", default=None, help="PS5 host/IP (default: $PS5_HOST)")
    parser.add_argument("--json", action="store_true", help="print raw JSON for data commands")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("hello")

    pad = sub.add_parser("pad")
    pad_sub = pad.add_subparsers(dest="pad_command", required=True)
    pad_sub.add_parser("open")
    pad_sub.add_parser("close")
    pad_sub.add_parser("status")
    pad_sub.add_parser("release")

    press = sub.add_parser("press")
    press.add_argument("buttons")
    press.add_argument("--ms", type=int, default=120)

    seq = sub.add_parser("seq")
    seq.add_argument("steps", nargs="+")
    seq.add_argument("--ms", type=int, default=120)

    shot = sub.add_parser("shot")
    shot.add_argument("-o", "--output", default=None)
    shot.add_argument("--max-width", type=int, default=1280)

    sub.add_parser("apps")

    launch = sub.add_parser("launch")
    launch.add_argument("title_id")

    sub.add_parser("home")
    sub.add_parser("close-app")

    ls = sub.add_parser("ls")
    ls.add_argument("path")

    get = sub.add_parser("get")
    get.add_argument("remote")
    get.add_argument("local", nargs="?", default=None)

    put = sub.add_parser("put")
    put.add_argument("local")
    put.add_argument("remote")

    rm = sub.add_parser("rm")
    rm.add_argument("path")

    sub.add_parser("df")

    notify = sub.add_parser("notify")
    notify.add_argument("text", nargs="+")

    sub.add_parser("info")
    sub.add_parser("quit")

    return parser


def _put_recursive(client: JokerClient, local: Path, remote: str) -> None:
    if local.is_dir():
        client.fs_mkdir(remote)
        for child in sorted(local.iterdir()):
            _put_recursive(client, child, f"{remote}/{child.name}")
        return
    data = local.read_bytes()
    client.fs_put(remote, data)
    print(f"put {local} -> {remote} ({len(data)} bytes)")


def _run(args: argparse.Namespace, client: JokerClient) -> int:
    cmd = args.command
    if cmd == "hello":
        result = client.hello()
        _print_json(result) if args.json else print(result)
    elif cmd == "pad":
        if args.pad_command == "open":
            result = client.pad_open()
            _print_json(result) if args.json else print(result)
        elif args.pad_command == "close":
            client.pad_close()
            print("closed")
        elif args.pad_command == "status":
            result = client.pad_status()
            _print_json(result) if args.json else print(result)
        elif args.pad_command == "release":
            client.pad_release()
            print("released")
    elif cmd == "press":
        client.pad_press(args.buttons, ms=args.ms)
        print("pressed")
    elif cmd == "seq":
        wire_steps = [_translate_seq_step(s, args.ms) for s in args.steps]
        result = client.pad_seq(wire_steps)
        _print_json(result) if args.json else print(result)
    elif cmd == "shot":
        screenshot = client.shot()
        png = screenshot.to_png(max_width=args.max_width)
        out_path = args.output or f"screenshot.png"
        Path(out_path).write_bytes(png)
        print(out_path)
    elif cmd == "apps":
        result = client.app_list()
        _print_json(result) if args.json else print(result)
    elif cmd == "launch":
        result = client.app_launch(args.title_id)
        _print_json(result) if args.json else print(result)
    elif cmd == "home":
        client.app_home()
        print("home")
    elif cmd == "close-app":
        client.app_close()
        print("closed")
    elif cmd == "ls":
        result = client.fs_ls(args.path)
        _print_json(result) if args.json else print(result)
    elif cmd == "get":
        data = client.fs_get(args.remote)
        local = args.local or os.path.basename(args.remote)
        Path(local).write_bytes(data)
        print(local)
    elif cmd == "put":
        _put_recursive(client, Path(args.local), args.remote)
    elif cmd == "rm":
        client.fs_rm(args.path)
        print("removed")
    elif cmd == "df":
        result = client.fs_df()
        _print_json(result) if args.json else print(result)
    elif cmd == "notify":
        client.notify(" ".join(args.text))
        print("notified")
    elif cmd == "info":
        result = client.sys_info()
        _print_json(result) if args.json else print(result)
    elif cmd == "quit":
        client.quit()
        print("quit")
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    client = JokerClient(host=args.host)
    try:
        sys.exit(_run(args, client))
    except JokerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
