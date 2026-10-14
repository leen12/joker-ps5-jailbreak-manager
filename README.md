# Joker PS5 Control

A fully open-source tool for remotely controlling a jailbroken PS5 from your PC, AI agents, or MCP-compatible tools.

**What it does:** Joker runs a tiny payload on your PS5 that creates a virtual DualSense controller and exposes a TCP command server. From your PC you get a Python client, a CLI, and an MCP server — everything you need to press buttons, take screenshots, launch games, and manage files on your console.

## Features

- **Virtual Controller** — Create a virtual DualSense pad via the PS5's own HID subsystem. Press buttons, move sticks, hold triggers, run input sequences — all timed on the console for frame-perfect inputs
- **Screenshots** — Capture the screen by holding the Create button (the console's native screenshot path), download the image, and auto-delete it from the gallery
- **App Management** — List installed games, launch titles, go home, close the running app
- **File Transfer** — List, read, write, and delete files on the console. Check disk space across all mount points
- **Notifications** — Show popup notifications on the PS5 screen
- **MCP Server** — 17 tools for AI agents (Claude, etc.) to drive the console directly

## Architecture

```
┌─────────────┐   TCP 9310   ┌──────────────┐
│  PC Client   │────────────▶│  jokerd.elf  │
│  (Python)    │◀────────────│  (PS5)       │
│              │             │              │
│  • CLI       │   line      │  • /dev/hid  │
│  • MCP server│   protocol  │  • sceLncUtil│
│  • Library   │             │  • filesystem│
└─────────────┘             └──────────────┘
```

## Requirements

**Console side:**
- PS5 with jailbreak (firmware ≤ 12.40 with etaHEN, or similar)
- Ability to load ELF payloads (via websrv, PS5 Payload Injector, etc.)

**PC side:**
- Python 3.10+
- `uv` (recommended) or `pip`

**Building the payload (optional — prebuilt ELFs in Releases):**
- Linux with clang-18, lld-18
- [PS5 Payload SDK v0.44+](https://github.com/ps5-payload-dev/sdk)

## Quick Start

### 1. Load the payload

Send `jokerd.elf` to your PS5 via the payload injector on port 9020:

```bash
curl -T payload/jokerd.elf http://PS5_IP:9020
```

The PS5 will show a notification: "Joker v0.1.0 — port 9310"

### 2. Install the Python client

```bash
cd client
uv pip install -e ".[dev]"
```

### 3. Use the CLI

```bash
# Set your PS5 IP
export PS5_HOST=192.168.1.100

# Check connection
jokerps5 hello

# Open the virtual controller
jokerps5 pad-open

# Press cross
jokerps5 press cross

# Take a screenshot
jokerps5 shot --save screenshot.jpg

# Launch a game
jokerps5 app-launch PPSA01234

# List files
jokerps5 ls /user/app

# Check disk space
jokerps5 df
```

### 4. Use as MCP server (for AI agents)

```bash
jokerps5-mcp
```

This exposes 17 tools: `status`, `open_controller`, `close_controller`, `press`, `hold`, `release_all`, `sequence`, `screenshot`, `list_apps`, `launch`, `go_home`, `close_app`, `list_files`, `pull`, `push`, `disk_space`, `notify`.

### 5. Use as a Python library

```python
from jokerps5 import JokerClient

with JokerClient("192.168.1.100") as ps5:
    ps5.hello()
    ps5.pad_open()
    ps5.press("cross", 200)
    img = ps5.shot()
    img.save("screen.png")
    ps5.pad_close()
```

## Building the Payload

```bash
# Install the SDK
git clone https://github.com/ps5-payload-dev/sdk /opt/ps5-payload-sdk
cd /opt/ps5-payload-sdk && make

# Build jokerd.elf
cd payload
make PS5_PAYLOAD_SDK=/opt/ps5-payload-sdk
```

## Wire Protocol

See [docs/protocol.md](docs/protocol.md) for the complete protocol specification.

The protocol is line-based (UTF-8, newline-terminated). Every command gets one JSON reply. Binary data (file transfers, screenshots) is announced by a `"bytes": N` field in the reply, followed by exactly N raw bytes.

## Prior Art & Credits

This project was built from scratch, but studied and learned from these excellent open-source projects:

| Project | Author | License | What we learned |
|---------|--------|---------|-----------------|
| [PS5_DevLoop](https://github.com/garyriches/PS5_DevLoop) | Gary Riches | GPL-3.0 | Pad ABI, klog device-id discovery, screenshot via Create hold, insert thread pattern |
| [ps5-mcp](https://github.com/dcoles/ps5-mcp) | David Coles | MIT | /dev/hid ioctl constants, sceLncUtil app control, compact pad report layout |
| [ps5-payload-dev/sdk](https://github.com/ps5-payload-dev/sdk) | John Googler | ISC | Build toolchain, stub libraries, kernel helpers |
| [ps5-payload-dev/websrv](https://github.com/ps5-payload-dev/websrv) | John Googler | ISC | Payload loading, CI/CD patterns |

## License

MIT — see [LICENSE](LICENSE).
