# Joker PS5 Control

A fully open-source tool for remotely controlling a jailbroken PS5 from your PC, AI agents, or MCP-compatible tools.

**What it does:** Joker runs a tiny payload on your PS5 that creates a virtual DualSense controller and exposes a TCP command server. From your PC you get a Python client, a CLI, and an MCP server — everything you need to press buttons, take screenshots, launch games, and manage files on your console.

## Features

- **Virtual Controller** — Create a virtual DualSense pad via libScePad when available, with a direct `/dev/hid` fallback proven on firmware 12.40. Press buttons, move sticks, hold triggers, and run console-timed sequences
- **Screenshots** — Capture the whole screen through Create, retrieve an existing gallery image without deletion, or request the next rendered frame directly from a cooperating custom app
- **App Management** — List installed games, launch titles, go home, close the running app
- **File Transfer** — List, read, write, and delete files on the console. Check disk space across all mount points
- **Notifications** — Show popup notifications on the PS5 screen
- **Administration** — Verified backups/restores, rollback-safe text edits and profiles, recursive homebrew deployment, ShadowMount registration, health audits, and a local web dashboard
- **App Workshop** — Create PS5 app folders, metadata and 512x512 icons; validate identity, layout, artwork and SHA-256 manifests before deployment
- **MCP Server** — Controller, app, filesystem, and administration tools for AI agents

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
- Windows: PowerShell plus the repository-local `.toolchain` prepared for `payload/build.ps1`
- Linux: clang-18, lld-18, and PS5 Payload SDK v0.44+
- [PS5 Payload SDK v0.44+](https://github.com/ps5-payload-dev/sdk)

## Quick Start

### 1. Load the payload

Start an ELF loader such as [ps5-payload-dev/elfldr](https://github.com/ps5-payload-dev/elfldr), then send `jokerd.elf` to its TCP port (normally 9021):

```bash
payloaduild.cmd           # compile + link (no PowerShell needed)
python payload/deploy.py PS5_IP   # upload, restart, wait for hello
```

`payload\deploy.ps1 -HostName PS5_IP -Build` does the same from PowerShell.

The deploy helper defaults to Payload Manager on port 8084: it uploads a versioned ELF, gracefully stops an existing jokerd through the protocol, launches the replacement, and requires a valid `hello` response before reporting success. It never sends the payload to etaHEN DPIv2 on port 12800.

The PS5 will show a notification: "Joker v0.1.0 — port 9310"

For parallel diagnostics, `payload\deploy.ps1 -HostName PS5_IP -Transport Websrv -JokerPort 9311` uses websrv's `/elfldr` endpoint and passes the custom port as argv, letting a new build run beside the primary daemon. `-Transport Auto` selects Payload Manager for port 9310 and websrv for custom ports. `quit` stops the selected daemon cleanly.

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
jokerps5 pad open

# Press cross
jokerps5 press cross

# Take a screenshot
jokerps5 shot --output screenshot.png

# Retrieve the newest HDR capture unchanged
jokerps5 shot --backend latest --raw --output newest.jxr

# Launch a game
jokerps5 launch PPSA01234

# List files
jokerps5 ls /user/app

# Check disk space
jokerps5 df
```

### 4. Use as MCP server (for AI agents)

```bash
jokerps5-mcp
```

This includes controller, screenshot, app, file, health, backup, restore, rollback-safe edit, and homebrew-deployment tools.

### Local dashboard

```bash
jokerps5 --host PS5_IP dashboard
```

Open `http://127.0.0.1:8745`. The dashboard shows live thermals, storage, controller state, installed apps and filesystem contents. It provides controller navigation, app launch, Home, notifications, file upload/download, directory creation and deletion, plus an optimistic-concurrency text editor that saves a timestamped rollback copy before every verified write.

It binds only to localhost by default. A non-local bind requires authentication:

```bash
jokerps5 --host PS5_IP dashboard --bind 0.0.0.0 --token "use-a-long-random-token"
```

Open the printed URL containing `#token=...`. The fragment remains browser-side; API calls send it as a bearer token. API requests without the token receive HTTP 401. Keep firewall exposure limited to the trusted LAN.

### Administration workflows

```bash
# Read-only snapshot of firmware, temperatures, disks, controller and running app
jokerps5 --json health

# Evaluate temperatures, free-space ratios, duplicate title IDs and controller readiness
jokerps5 --json audit

# Back up files/directories with a SHA-256 restore manifest
jokerps5 backup backups/before-change /data/etaHEN /data/pldmgr

# Replace one exact string; a timestamped rollback file is written beside the target
jokerps5 edit-text /data/etaHEN/config.ini "DPI_v2=0" "DPI_v2=1"

# Restore a backup; every local and uploaded file is hash-verified
jokerps5 restore backups/before-change

# Deploy a loose homebrew app and ask ShadowMount to register/rescan it
jokerps5 deploy-homebrew ./MyApp MYAPP00001 --shadowmount-url http://PS5_IP:10101
```

`deploy-homebrew` requires exactly one of `eboot.bin` or `eboot.elf`, plus `sce_sys/param.json` and `sce_sys/icon0.png`. Files use jokerd's atomic `.part` + rename path and are read back for SHA-256 verification. Native `.elf`, `.bin`, `.prx`, and `.sprx` files are uploaded as mode 0755 and their remote execute bits are verified.

### Create and validate a custom app

```bash
# Produces ./apps/JOKR00001 with metadata and a generated 512x512 icon
jokerps5 create-app ./apps JOKR00001 "My PS5 App" ./build/my-app.elf

# Use custom art or create a Media-tab app
jokerps5 create-app ./apps JOKR00002 "Media Tool" ./build/media.bin \
  --icon ./art/icon0.png --category media --version 01.001.000

jokerps5 --json validate-app ./apps/JOKR00001
jokerps5 deploy-homebrew ./apps/JOKR00001 JOKR00001 \
  --shadowmount-url http://PS5_IP:10101
```

The validator requires a four-letter/five-digit title ID, coordinated content ID, `NN.NNN.NNN` content version, exactly one executable, and a valid 512x512 PNG icon. It returns a SHA-256 manifest for every file.

Custom apps can provide reliable, fast frame capture using the reusable [`joker_capture.h`](integrations/joker_capture.h) hook. See [cooperative title-frame capture](docs/title-capture.md) for the renderer contract and CLI commands.

### Generate a complete source project

```bash
# Lightweight native C++20 app
jokerps5 project-create native ./MyNativeApp PPSA21001 "My Native App"

# RADV/Vulkan app with the upstream graphics examples as its foundation
jokerps5 project-create vulkan ./MyVulkanApp PPSA21002 "My Vulkan App"

cd MyNativeApp
jokerps5 project-doctor .
jokerps5 project-build .
jokerps5 --host PS5_IP project-deploy . --shadowmount-url http://PS5_IP:10101
```

Both adapters use the current upstream generator, coordinate PS5 identity metadata, install Joker's cooperative capture header, record reproducible project metadata, and retain the upstream build system. See [native and Vulkan project workflows](docs/project-workflows.md).

### Reversible configuration profiles

Profiles group exact text replacements into one transaction:

```json
{
  "name": "my-console-profile",
  "changes": [
    {"path": "/data/example/config.ini", "old": "option=0", "new": "option=1"}
  ]
}
```

```bash
jokerps5 apply-profile ./profiles/my-console-profile.json
```

Every path and match is preflighted before writes begin. Timestamped remote backups are created first; uploaded results are read back and verified; a failure rolls back all changes already applied.

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

On Windows:

```powershell
payload\build.ps1
```

## Wire Protocol

See [docs/protocol.md](docs/protocol.md) for the complete protocol specification.

The protocol is line-based (UTF-8, newline-terminated). Every command gets one JSON reply. Binary data (file transfers, screenshots) is announced by a `"bytes": N` field in the reply, followed by exactly N raw bytes.

## Prior Art & Credits

This project extends proven patterns and formats from these open-source projects:

| Project | Author | License | What we learned |
|---------|--------|---------|-----------------|
| [PS5_DevLoop](https://github.com/garyriches/PS5_DevLoop) | Gary Riches | GPL-3.0 | Pad ABI, klog device-id discovery, screenshot via Create hold, insert thread pattern |
| [ps5-mcp](https://github.com/dcoles/ps5-mcp) | David Coles | MIT | /dev/hid ioctl constants, sceLncUtil app control, compact pad report layout |
| [ps5-payload-dev/sdk](https://github.com/ps5-payload-dev/sdk) | John Googler | ISC | Build toolchain, stub libraries, kernel helpers |
| [ps5-payload-dev/websrv](https://github.com/ps5-payload-dev/websrv) | John Googler | ISC | Payload loading, CI/CD patterns |
| [Nativehbl](https://github.com/Rufidj/Nativehbl) | Rufidj | GPL-3.0 | Directory-style homebrew layout |
| [ps5-native-app-boilerplate](https://github.com/blackbearreloaded/ps5-native-app-boilerplate) | BlackBear | MIT | Native app metadata and presentation-asset conventions |

## License

MIT — see [LICENSE](LICENSE).
