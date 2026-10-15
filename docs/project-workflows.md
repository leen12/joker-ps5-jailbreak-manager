# Native and Vulkan project workflows

Joker wraps established upstream generators instead of maintaining incompatible copies of their SDK and rendering stacks.

## Native application

```bash
jokerps5 project-create native ./MyNativeApp PPSA21001 "My Native App"
cd MyNativeApp
jokerps5 project-inspect .
jokerps5 project-doctor .
jokerps5 project-build .
jokerps5 --host PS5_IP project-deploy . --shadowmount-url http://PS5_IP:10101
```

The default template is `blackbearreloaded/ps5-native-app-boilerplate`. Joker performs a shallow clone, coordinates every PS5 `param.json` identity field, installs `integrations/joker_capture.h`, and writes `.jokerps5.json` containing the project kind, identity, capture paths, and build commands.

This stack is the smaller choice for system utilities, launchers, dashboards and CPU-rendered interfaces. It provides a native C++20 application, reproducible Windows/WSL tooling and ShadowMount-ready folder output.

On Windows, the upstream native build requires WSL with an installed Linux distribution. `project-build` reports the upstream failure unchanged when WSL has no distribution. Install and initialize a distribution before building; project generation and inspection remain available without WSL.

## Vulkan application

```bash
jokerps5 project-create vulkan ./MyVulkanApp PPSA21002 "My Vulkan App"
cd MyVulkanApp
jokerps5 project-inspect .
jokerps5 project-doctor .
jokerps5 project-build .
jokerps5 --host PS5_IP project-deploy . --shadowmount-url http://PS5_IP:10101
```

The Vulkan adapter clones `hotshotz79/PS5_VulkanTemplate` into a temporary checkout and calls its own `ps5/tools/new-title.py`. The upstream generator requires a title ID matching `PPSA` plus five digits. Joker then verifies and coordinates the generated manifest and installs its capture integration.

The Vulkan bootstrap/build scripts require a Bash/Linux environment and download the pinned PS5 Vulkan, Mesa and payload-SDK stack declared upstream.

Use this stack for GPU-heavy applications. Its reference suite covers glTF, physically based rendering, shadows, deferred rendering, bloom, MSAA, instancing, indirect draws, compute particles, descriptor indexing, mesh shaders, ray queries and ImGui. The upstream PS5 port targets the console display at 3840x2160 and includes deterministic host-versus-console frame comparison.

## Local template checkouts

Avoid another network clone by supplying an existing checkout:

```bash
jokerps5 project-create native ./MyApp PPSA21003 "My App" \
  --template D:/templates/ps5-native-app-boilerplate
```

Local templates are copied without `.git`, `build`, `dist`, or `.deps`. Remote templates are shallow-cloned. The destination must not already exist.

## Capture integration

Every adapted project contains:

```text
integrations/joker_capture.h
.jokerps5.json
```

The metadata records the external and sandbox capture paths. Connect the supplied callback to the title's completed-frame path as described in [title-capture.md](title-capture.md). Vulkan projects can connect it to the template's existing swapchain readback path; native CPU-rendered projects can encode the completed canvas.

## Build output and deployment

`project-deploy` searches these standard outputs in order:

1. `dist/<TITLE_ID>`
2. `<TITLE_ID>`
3. `build/app/<TITLE_ID>`
4. One unambiguous nested `<TITLE_ID>/sce_sys/param.json`

It then uses Joker's verified application deployment: atomic upload, SHA-256 readback, executable-bit checks, optional ShadowMount manual registration, and rescan.
