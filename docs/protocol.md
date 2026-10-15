# Joker PS5 Control — wire protocol (v1)

The payload `jokerd.elf` runs on the console and listens on **TCP 9310**.

## Framing

- A client may send many commands over one connection.
- A command is one line of UTF-8 text ending in `\n`. Words are separated by single spaces.
  The maximum line length is 1024 bytes.
- Every command gets exactly one reply line: a compact JSON object ending in `\n`.
- Every reply has `"ok": true|false`. Failed replies have `"error": "<message>"`.
- A reply may announce raw bytes with `"bytes": N`. Exactly N raw bytes follow the reply line.
- An upload command (`put`) carries `N` in the command. The client sends exactly N raw bytes
  right after the command line. The reply comes after the bytes are received.
- Numbers in commands are decimal unless noted. Button masks are hex with or without `0x`.

## Commands

| Command | Reply fields | Notes |
|---|---|---|
| `hello` | `name`, `version`, `proto` (=1), `fw` (hex string), `pid`, `pad` (`"open"`/`"closed"`) | Use first to check the payload is alive |
| `pad open [user_id]` | `backend` (`"libScePad"` or `"direct-hid"`), `handle`, `user`, `steps` (array of strings) | `user_id` defaults to the foreground user. If libSceMbus cannot load, Joker falls back to the direct `/dev/hid` ioctl ABI. `steps` records every call and its return code |
| `pad close` | `steps` | Removes the device. Always call before stopping the payload |
| `pad status` | `open`, `backend`, `handle`, `user`, `inserts`, `last` (hex) | |
| `pad set <buttons> <lx> <ly> <rx> <ry> <l2> <r2>` | — | Hold this state until changed. Axes 0..255, 128 = centre |
| `pad press <buttons> <ms>` | — | Hold for `ms` (1..10000), then return to neutral. Timed on the console |
| `pad seq <step> <step> ...` | `steps_run` | Steps: `<buttons>:<ms>` press, `w<ms>` wait. Max 64 steps |
| `pad release` | — | Neutral state |
| `shot [hold_ms]` | `bytes`, `path`, `format` (`"jpg"`/`"jxr"`/`"png"`), `deleted` (hex) | Holds Create (default 1200 ms), waits up to 10 s for the new capture, sends it, deletes it from the console |
| `shot latest` | `bytes`, `path`, `format`, `backend` (`"gallery"`) | Sends the newest existing gallery capture without deleting it |
| `shot grab <request> <folder> [wait_ms]` | `bytes`, `path`, `format`, `backend` (`"title-grab"`) | Creates a request marker for a cooperating title, waits for a stable new image, sends it, and removes the temporary image |
| `shots` | `files` (array of `{path,size,mtime}`) | Newest 5 captures |
| `app list` | `apps` (array of `{id, name}`) | From `/user/app` and `/user/appmeta/<id>/param.json` |
| `app launch <title_id>` | `app_id` | |
| `app home` | — | Suspends the running game and shows the home screen |
| `app close` | — | Kills the running game |
| `app running` | `app_id` (or `-1`) | |
| `fs ls <path>` | `entries` (array of `{name, type ("f"/"d"/"l"), size, mtime}`) | |
| `fs stat <path>` | `type`, `size`, `mtime`, `mode` (octal string) | |
| `fs get <path>` | `bytes` | File contents follow |
| `fs put <path> <size> [mode_octal]` | `written` | Client sends `size` raw bytes after the line. Written to `<path>.part` then renamed |
| `fs mkdir <path>` | — | Creates parents |
| `fs rm <path>` | — | Files and empty folders only |
| `fs df` | `mounts` (array of `{path, total, free}`) | `/user`, `/data`, `/mnt/ext1`, `/mnt/usb0`, `/mnt/usb1` when present |
| `sys notify <text...>` | — | Shows a PS5 notification |
| `sys info` | `fw`, `temp_soc`, `temp_cpu` (when available), `uptime_s` | |
| `quit` | — | Closes the pad and stops the payload |

## Buttons (hex mask)

| Button | Bit | Button | Bit |
|---|---|---|---|
| create | `0x1` | l3 | `0x2` |
| r3 | `0x4` | options | `0x8` |
| up | `0x10` | right | `0x20` |
| down | `0x40` | left | `0x80` |
| l2 | `0x100` | r2 | `0x200` |
| l1 | `0x400` | r1 | `0x800` |
| triangle | `0x1000` | circle | `0x2000` |
| cross | `0x4000` | square | `0x8000` |
| touchpad | `0x100000` | | |

The PS button cannot be sent through a virtual pad. Use `app home`.
When `l2`/`r2` bits are set and their analog value is 0, the payload sends 255.

## Safety rules the payload enforces

- `fs rm` refuses `/`, `/system*`, `/preinst*`, `/update`, `/user` itself and any path containing `..`.
- `fs put` and `fs rm` refuse paths outside `/data`, `/user/data`, `/mnt/ext1`, `/mnt/usb*`, `/user/av_contents`, `/user/app`, `/user/homebrew`.
- One client at a time runs a command. Others wait (queued by the accept loop).
- When a client disconnects while buttons are held, the payload releases to neutral.
- Higher-level administration in the Python client creates SHA-256 manifests, stages writes through
  the payload's `.part` + rename flow, and verifies uploaded bytes by reading them back.
- Text edits create a timestamped sibling backup before replacement. Restore validates local hashes
  before writing and verifies each remote file afterward.
