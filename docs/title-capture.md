# Cooperative title-frame capture

The PS5 screenshot service requires an application-thread context that a standalone payload does not have. Joker therefore supports a second capture backend for custom apps: the title writes its own next rendered frame when jokerd creates a request marker.

## Console paths

For title `JOKR00001`, use:

```text
request: /data/homebrew/JOKR00001/joker-shot-request
folder:  /data/homebrew/JOKR00001/joker-shots
```

Inside the title sandbox, the same locations are:

```text
request: /app0/joker-shot-request
folder:  /app0/joker-shots
```

Create the output directory with mode `0777`. Frame files must be writable and removable by the resident payload.

## C integration

Copy `integrations/joker_capture.h` into the title. Provide a callback that writes the current completed frame as PNG, JPEG, or JXR:

```c
#include "joker_capture.h"

static int write_current_frame_png(const char *path, void *renderer)
{
    return renderer_write_screenshot_png(renderer, path);
}

void app_frame(AppRenderer *renderer)
{
    render_frame(renderer);
    joker_capture_poll(
        "/app0/joker-shot-request",
        "/app0/joker-shots",
        "png",
        write_current_frame_png,
        renderer);
    present_frame(renderer);
}
```

The helper uses removal of the request marker as an atomic claim, creates a unique output filename, invokes the writer, applies mode `0666`, and removes incomplete output on failure.

For Vulkan applications, call the hook after rendering has completed and after any required image-to-host synchronization, but before recycling the captured swapchain image. For engines with a built-in screenshot command, the callback can enqueue that command instead, provided the resulting file appears in the configured output directory.

## Capture

```bash
jokerps5 shot --backend grab \
  --request /data/homebrew/JOKR00001/joker-shot-request \
  --folder /data/homebrew/JOKR00001/joker-shots \
  --wait-ms 5000 \
  --output frame.png
```

Jokerd waits until a new file stops growing, transfers it, and removes that temporary frame. If the title never consumed the marker, the error says `request was not consumed`. If the title consumed it but did not produce an image, the error says `request was consumed`.

## Other backends

```bash
# Whole composited screen through the console Create shortcut
jokerps5 shot --backend create --output screen.png

# Newest existing gallery capture; preserved on the console
jokerps5 shot --backend latest --raw --output newest.jxr
```

Use `--raw` for HDR/JXR when the optional `imagecodecs` decoder is not installed. Install `jokerps5[jxr]` to convert JXR captures to PNG.
