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
from .admin import AdminManager
from .toolkit import AppProject, ProfileManager, ProjectAdapter


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
    parser.add_argument("--port", type=int, default=None, help="jokerd port (default: $PS5_PORT or 9310)")
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
    shot.add_argument("--raw", action="store_true", help="save the console image unchanged; useful for JXR/HDR")
    shot.add_argument("--backend", choices=("create", "latest", "grab"), default="create")
    shot.add_argument("--request", default=None, help="grab request path in the running title folder")
    shot.add_argument("--folder", default=None, help="grab output folder watched by jokerd")
    shot.add_argument("--wait-ms", type=int, default=3000)

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

    backup = sub.add_parser("backup", help="create a verified local backup of remote paths")
    backup.add_argument("destination")
    backup.add_argument("paths", nargs="+")

    restore = sub.add_parser("restore", help="restore a Joker backup and verify every file")
    restore.add_argument("source")

    edit = sub.add_parser("edit-text", help="replace text once with a remote rollback copy")
    edit.add_argument("remote")
    edit.add_argument("old")
    edit.add_argument("new")

    deploy = sub.add_parser("deploy-homebrew", help="upload and verify a homebrew application")
    deploy.add_argument("local_dir")
    deploy.add_argument("title_id")
    deploy.add_argument("--shadowmount-url", default=None)

    create_app = sub.add_parser("create-app", help="create a validated native homebrew folder")
    create_app.add_argument("destination")
    create_app.add_argument("title_id")
    create_app.add_argument("name")
    create_app.add_argument("executable")
    create_app.add_argument("--icon", default=None)
    create_app.add_argument("--version", default="01.000.000")
    create_app.add_argument("--category", choices=("game", "media"), default="game")

    validate_app = sub.add_parser("validate-app", help="validate app metadata, art, and hashes")
    validate_app.add_argument("directory")

    apply_profile = sub.add_parser("apply-profile", help="apply a rollback-safe JSON configuration profile")
    apply_profile.add_argument("profile")

    project_create = sub.add_parser("project-create", help="create a native or Vulkan PS5 project from upstream tooling")
    project_create.add_argument("kind", choices=("native", "vulkan"))
    project_create.add_argument("destination")
    project_create.add_argument("title_id")
    project_create.add_argument("name")
    project_create.add_argument("--template", default=None, help="local template checkout or alternate Git URL")

    project_inspect = sub.add_parser("project-inspect", help="inspect Joker project identity and build commands")
    project_inspect.add_argument("directory")

    project_doctor = sub.add_parser("project-doctor", help="run the upstream project environment doctor/bootstrap")
    project_doctor.add_argument("directory")

    project_build = sub.add_parser("project-build", help="run the upstream project build")
    project_build.add_argument("directory")

    project_deploy = sub.add_parser("project-deploy", help="locate, upload, verify, and register a built project")
    project_deploy.add_argument("directory")
    project_deploy.add_argument("--shadowmount-url", default=None)

    dashboard = sub.add_parser("dashboard", help="run the local web control dashboard")
    dashboard.add_argument("--bind", default="127.0.0.1")
    dashboard.add_argument("--listen", type=int, default=8745)
    dashboard.add_argument("--token", default=None, help="required bearer token for non-localhost binding")

    sub.add_parser("health", help="collect a read-only console health snapshot")
    sub.add_parser("audit", help="audit thermals, storage, apps, and controller readiness")
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


def _run(args: argparse.Namespace, client: JokerClient | None) -> int:
    cmd = args.command
    local_commands = {"create-app", "validate-app", "project-create", "project-inspect", "project-doctor", "project-build"}
    if cmd not in local_commands and client is None:
        raise ValueError("this command requires --host or PS5_HOST")
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
        if args.backend == "latest":
            screenshot = client.shot_latest()
        elif args.backend == "grab":
            if not args.request or not args.folder:
                raise ValueError("--backend grab requires --request and --folder")
            screenshot = client.shot_grab(args.request, args.folder, args.wait_ms)
        else:
            screenshot = client.shot()
        image = screenshot.data if args.raw else screenshot.to_png(max_width=args.max_width)
        out_path = args.output or f"screenshot.{screenshot.format if args.raw else 'png'}"
        Path(out_path).write_bytes(image)
        print(f"{out_path} ({screenshot.backend})")
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
    elif cmd == "backup":
        result = AdminManager(client).backup(args.paths, Path(args.destination))
        _print_json(result) if args.json else print(f"backed up {len(result['files'])} files")
    elif cmd == "restore":
        result = AdminManager(client).restore(Path(args.source))
        _print_json(result) if args.json else print(f"restored {result['restored']} files")
    elif cmd == "edit-text":
        result = AdminManager(client).edit_text(args.remote, args.old, args.new)
        _print_json(result) if args.json else print(f"updated; rollback copy: {result['backup']}")
    elif cmd == "deploy-homebrew":
        result = AdminManager(client).deploy_homebrew(
            Path(args.local_dir), args.title_id, shadowmount_url=args.shadowmount_url
        )
        _print_json(result) if args.json else print(
            f"deployed {result['files']} files ({result['bytes']} bytes) to {result['remote_root']}"
        )
    elif cmd == "create-app":
        result = AppProject.create(
            Path(args.destination), args.title_id, args.name, Path(args.executable),
            icon=Path(args.icon) if args.icon else None, version=args.version, category=args.category,
        )
        _print_json(result) if args.json else print(f"created {result['root']} ({len(result['files'])} files)")
    elif cmd == "validate-app":
        result = AppProject.validate(Path(args.directory))
        _print_json(result) if args.json else print(result)
        if not result["ok"]:
            return 2
    elif cmd == "apply-profile":
        result = ProfileManager(client).apply(Path(args.profile))
        _print_json(result) if args.json else print(f"applied {result['name']} ({len(result['changed'])} files)")
    elif cmd == "project-create":
        result = ProjectAdapter.create(args.kind, Path(args.destination), args.title_id, args.name, template=args.template)
        _print_json(result) if args.json else print(f"created {result['kind']} project at {result['root']}")
    elif cmd == "project-inspect":
        result = ProjectAdapter.inspect(Path(args.directory))
        _print_json(result) if args.json else print(result)
    elif cmd == "project-doctor":
        result = ProjectAdapter.run_action(Path(args.directory), "doctor")
        _print_json(result) if args.json else print("project doctor passed")
    elif cmd == "project-build":
        result = ProjectAdapter.run_action(Path(args.directory), "build")
        _print_json(result) if args.json else print("project build passed")
    elif cmd == "project-deploy":
        directory = Path(args.directory)
        project = ProjectAdapter.inspect(directory)
        app = ProjectAdapter.find_app(directory)
        result = AdminManager(client).deploy_homebrew(
            app, project["title_id"], shadowmount_url=args.shadowmount_url
        )
        _print_json(result) if args.json else print(f"deployed {project['title_id']} from {app}")
    elif cmd == "dashboard":
        from .dashboard import serve

        serve(client.host, client.port, bind=args.bind, listen=args.listen, token=args.token)
    elif cmd == "health":
        result = AdminManager(client).health()
        _print_json(result) if args.json else print(result)
    elif cmd == "audit":
        result = AdminManager(client).audit()
        _print_json(result) if args.json else print(result)
    elif cmd == "quit":
        client.quit()
        print("quit")
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    port = args.port if args.port is not None else int(os.environ.get("PS5_PORT", "9310"))
    local_commands = {"create-app", "validate-app", "project-create", "project-inspect", "project-doctor", "project-build"}
    client = None if args.command in local_commands else JokerClient(host=args.host, port=port)
    try:
        sys.exit(_run(args, client))
    except JokerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
