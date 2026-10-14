/* Joker PS5 Control — resident command server.
 * SPDX-License-Identifier: MIT
 *
 * Runs on the console, listens on TCP 9310, speaks the protocol in docs/protocol.md.
 * Prior art studied (our own implementation, not copied code):
 *   PS5_DevLoop by Gary Riches (GPL-3.0)   — pad ABI, klog device-id discovery, screenshot via Create hold
 *   ps5-mcp by David Coles (MIT)           — /dev/hid ioctls, sceLncUtil app control
 *   ps5-payload-dev SDK v0.44              — build toolchain, stub libraries
 */

#include <arpa/inet.h>
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include <ps5/kernel.h>
#include "util.h"

/* ── Constants ───────────────────────────────────────────────────────── */

#define LINE_MAX_LEN    1024
#define MAX_SEQ_STEPS   64
#define DEVICE_TYPE     3
#define UNASSIGNED_USER 1
#define ASSIGNMENT_PENDING 0x803b0006u
#define INSERT_INTERVAL_US 8000
#define BUTTON_CREATE   0x00000001u
#define BUTTON_L2       0x00000100u
#define BUTTON_R2       0x00000200u
#define DEFAULT_CREATE_HOLD_MS 1200
#define SHOT_WAIT_MS    10000
#define KLOG_PORT       3232
#define NEWEST_SHOTS    5

/* ── Pad data (120-byte sample) ──────────────────────────────────────── */

typedef struct pad_touch {
    uint16_t x, y;
    uint8_t id;
    uint8_t reserved[3];
} pad_touch_t;

typedef struct pad_data {
    uint32_t buttons;
    uint8_t lx, ly, rx, ry;
    uint8_t l2, r2;
    uint8_t reserved0[2];
    float orientation[4];
    float acceleration[3];
    float angular_velocity[3];
    uint8_t touch_count;
    uint8_t reserved1[7];
    pad_touch_t touch[2];
    uint8_t connected;
    uint8_t reserved2[3];
    uint64_t timestamp;
    uint8_t extension[16];
    uint8_t connected_count;
    uint8_t reserved3[15];
} pad_data_t;
_Static_assert(sizeof(pad_data_t) == 120, "pad sample must be 120 bytes");

typedef struct add_device_param {
    int32_t size;
    int32_t user_id;
    int32_t unknown[6];
} add_device_param_t;

/* ── SDK externs ─────────────────────────────────────────────────────── */

extern int32_t scePadInit(void);
extern int32_t scePadSetProcessPrivilege(int32_t privilege);
extern int32_t scePadVirtualDeviceAddDevice(void *param, int32_t device_type);
extern int32_t scePadVirtualDeviceInsertData(int32_t handle, const void *data);
extern int32_t scePadVirtualDeviceDeleteDevice(int32_t handle);
extern int sceUserServiceInitialize(void *param);
extern int sceUserServiceGetForegroundUser(int *user_id);

/* Resolved at runtime */
static int32_t (*mbus_bind)(uint64_t device_id, int32_t user_id);
__attribute__((weak)) int sceLncUtilInitialize(void);
__attribute__((weak)) uint32_t sceLncUtilLaunchApp(const char *title_id, const char **argv, void *param);
__attribute__((weak)) int sceLncUtilGetAppIdOfRunningBigApp(void);
__attribute__((weak)) int sceLncUtilSuspendApp(int app_id, int flag);
__attribute__((weak)) int sceLncUtilKillApp(int app_id);

typedef struct content_delete_param {
    uint64_t unknown0;
    uint64_t buffer_size;
    uint64_t unknown[6];
} content_delete_param_t;

static int32_t (*content_delete_init)(content_delete_param_t *param);
static int32_t (*content_delete_by_path)(const char *path);
static int32_t (*content_delete_term)(void);

/* ── Launch param (same layout as public PS5 homebrew) ───────────────── */

typedef struct launch_param {
    uint32_t size;
    uint32_t user_id;
    uint32_t app_opt;
    uint64_t crash_report;
    uint32_t check_flag;
} launch_param_t;

/* ── Global pad state ────────────────────────────────────────────────── */

static struct {
    pthread_mutex_t lock;
    bool libs_ready;
    bool open;
    int32_t handle;
    uint64_t device_id;
    int32_t user_id;
    int32_t last_insert;
    uint32_t inserts;
    pad_data_t state;
} g_pad = {.lock = PTHREAD_MUTEX_INITIALIZER, .handle = -1, .user_id = -1};

static bool g_launcher_ready;
static bool g_delete_ready;

/* ── Helpers ─────────────────────────────────────────────────────────── */

static uint64_t monotonic_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}

static void pad_neutral(pad_data_t *d) {
    memset(d, 0, sizeof(*d));
    d->lx = d->ly = d->rx = d->ry = 128;
    d->orientation[3] = 1.0f;
    d->acceleration[1] = 1.0f;
    d->connected = 1;
    d->connected_count = 1;
}

static int32_t pad_insert(int32_t handle, const pad_data_t *d) {
    pad_data_t stamped = *d;
    stamped.timestamp = monotonic_us();
    return scePadVirtualDeviceInsertData(handle, &stamped);
}

/* ── Insert thread: keeps the held state arriving ────────────────────── */

static void *insert_thread(void *arg) {
    (void)arg;
    for (;;) {
        pthread_mutex_lock(&g_pad.lock);
        if (g_pad.open) {
            g_pad.last_insert = pad_insert(g_pad.handle, &g_pad.state);
            g_pad.inserts++;
        }
        pthread_mutex_unlock(&g_pad.lock);
        usleep(INSERT_INTERVAL_US);
    }
    return NULL;
}

/* ── Pad hold/release ────────────────────────────────────────────────── */

static void pad_hold(uint32_t buttons, int lx, int ly, int rx, int ry, int l2, int r2) {
    pthread_mutex_lock(&g_pad.lock);
    pad_neutral(&g_pad.state);
    g_pad.state.buttons = buttons;
    g_pad.state.lx = (uint8_t)lx;
    g_pad.state.ly = (uint8_t)ly;
    g_pad.state.rx = (uint8_t)rx;
    g_pad.state.ry = (uint8_t)ry;
    g_pad.state.l2 = (uint8_t)((buttons & BUTTON_L2) && l2 == 0 ? 255 : l2);
    g_pad.state.r2 = (uint8_t)((buttons & BUTTON_R2) && r2 == 0 ? 255 : r2);
    pthread_mutex_unlock(&g_pad.lock);
}

static void pad_release(void) {
    pad_hold(0, 128, 128, 128, 128, 0, 0);
}

/* ── Klog device-id discovery ────────────────────────────────────────── */

static int klog_open(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd >= 0) {
        struct sockaddr_in addr = {0};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = htons(KLOG_PORT);
        if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) == 0)
            return fd;
        close(fd);
    }
    return open("/dev/klog", O_RDONLY | O_NONBLOCK);
}

static uint64_t klog_device_added(const char *line) {
    if (!strstr(line, "DEVICE_ADDED")) return 0;
    const char *st = strstr(line, "subType");
    if (!st) st = strstr(line, "deviceType");
    if (st) {
        st += strcspn(st, ":=");
        if (strtoul(st + (*st != '\0'), NULL, 0) != 22) return 0;
    }
    const char *id = strstr(line, "DeviceId");
    if (!id) id = strstr(line, "deviceId");
    if (!id) return 0;
    id += strcspn(id, ":=");
    return strtoull(id + (*id != '\0'), NULL, 16);
}

static uint64_t klog_wait_device(int log, int wait_ms, char *steps, size_t ssize) {
    char line[1024];
    size_t used = 0;
    uint64_t devid = 0;
    uint64_t deadline = monotonic_us() + (uint64_t)wait_ms * 1000u;
    while (!devid && monotonic_us() < deadline) {
        struct pollfd pfd = {.fd = log, .events = POLLIN};
        if (poll(&pfd, 1, 100) <= 0) continue;
        char chunk[512];
        ssize_t n = read(log, chunk, sizeof(chunk));
        if (n <= 0) break;
        for (ssize_t i = 0; i < n && !devid; i++) {
            if (chunk[i] != '\n' && used + 1 < sizeof(line)) {
                line[used++] = chunk[i];
                continue;
            }
            line[used] = '\0';
            used = 0;
            devid = klog_device_added(line);
        }
    }
    if (!devid) step(steps, ssize, "klog=no-device-line");
    return devid;
}

/* ── Pad libraries ───────────────────────────────────────────────────── */

static int pad_libraries(char *steps, size_t ssize) {
    if (g_pad.libs_ready) return 0;
    elevate(steps, ssize);

    void *mbus = dlopen("/system/common/lib/libSceMbus.sprx", RTLD_NOW | RTLD_GLOBAL);
    if (!mbus) mbus = dlopen("libSceMbus.sprx", RTLD_NOW | RTLD_GLOBAL);
    if (!mbus) { step(steps, ssize, "mbus=not-loaded"); return -1; }
    mbus_bind = (int32_t (*)(uint64_t, int32_t))dlsym(mbus, "sceMbusBindDeviceWithUserId");
    step(steps, ssize, "mbus_bind=%s", mbus_bind ? "found" : "missing");

    int32_t rc = scePadInit();
    int32_t priv = scePadSetProcessPrivilege(1);
    step(steps, ssize, "padInit=0x%08x priv=0x%08x", (uint32_t)rc, (uint32_t)priv);
    if (rc) return -1;
    g_pad.libs_ready = true;
    return 0;
}

/* ── Find the handle InsertData accepts ──────────────────────────────── */

static int32_t pad_find_handle(int32_t added, const add_device_param_t *p,
                                uint64_t devid, char *steps, size_t ssize) {
    int32_t cands[10];
    int n = 0;
    if (added > 0) cands[n++] = added;
    for (int i = 0; i < 6; i++)
        if (p->unknown[i] > 0) cands[n++] = p->unknown[i];
    if (devid) {
        cands[n++] = (int32_t)(devid & 0xffffffffu);
        if (devid >> 32) cands[n++] = (int32_t)(devid >> 32);
    }
    pad_data_t neutral;
    pad_neutral(&neutral);
    for (int i = 0; i < n; i++) {
        int32_t rc = pad_insert(cands[i], &neutral);
        step(steps, ssize, "insert(0x%x)=0x%08x", (uint32_t)cands[i], (uint32_t)rc);
        if (rc == 0) return cands[i];
    }
    return -1;
}

/* ── Pad open / close ────────────────────────────────────────────────── */

static int cmd_pad_open(int32_t user_id, char *steps, size_t ssize) {
    if (g_pad.open) { step(steps, ssize, "already-open"); return 0; }
    if (pad_libraries(steps, ssize)) return -1;
    if (user_id <= UNASSIGNED_USER)
        sceUserServiceGetForegroundUser(&user_id);
    step(steps, ssize, "user=0x%08x", (uint32_t)user_id);

    int log = klog_open();
    if (log < 0) step(steps, ssize, "klog=unavailable");
    else { char drain[1024]; while (read(log, drain, sizeof(drain)) > 0); }

    add_device_param_t param = {0};
    param.size = (int32_t)sizeof(param);
    param.user_id = UNASSIGNED_USER;
    int32_t added = scePadVirtualDeviceAddDevice(&param, DEVICE_TYPE);
    step(steps, ssize, "add=0x%08x", (uint32_t)added);

    uint64_t devid = 0;
    if (log >= 0) { devid = klog_wait_device(log, 3000, steps, ssize); close(log); }
    step(steps, ssize, "device=0x%llx", (unsigned long long)devid);
    if (added && (uint32_t)added != ASSIGNMENT_PENDING && !devid) return -1;

    if (devid && user_id > UNASSIGNED_USER && mbus_bind)
        step(steps, ssize, "bind=0x%08x", (uint32_t)mbus_bind(devid, user_id));
    else
        step(steps, ssize, "bind=skipped");

    int32_t handle = pad_find_handle(added, &param, devid, steps, ssize);
    if (handle < 0) { g_pad.device_id = devid; return -1; }

    pthread_mutex_lock(&g_pad.lock);
    pad_neutral(&g_pad.state);
    g_pad.handle = handle;
    g_pad.device_id = devid;
    g_pad.user_id = user_id;
    g_pad.inserts = 0;
    g_pad.open = true;
    pthread_mutex_unlock(&g_pad.lock);
    return 0;
}

static void cmd_pad_close(char *steps, size_t ssize) {
    pthread_mutex_lock(&g_pad.lock);
    bool was = g_pad.open;
    int32_t h = g_pad.handle;
    g_pad.open = false;
    g_pad.handle = -1;
    pthread_mutex_unlock(&g_pad.lock);
    if (!was) { step(steps, ssize, "not-open"); return; }
    pad_data_t neutral;
    pad_neutral(&neutral);
    pad_insert(h, &neutral);
    step(steps, ssize, "delete=0x%08x", (uint32_t)scePadVirtualDeviceDeleteDevice(h));
}

/* ── Launcher ────────────────────────────────────────────────────────── */

static int launcher_init(void) {
    if (g_launcher_ready) return 0;
    if (!sceLncUtilInitialize) return ENOSYS;
    int rc = sceLncUtilInitialize();
    if (rc && (uint32_t)rc != 0x80940018u) return rc;
    g_launcher_ready = true;
    return 0;
}

static int running_app(void) {
    if (!sceLncUtilGetAppIdOfRunningBigApp) return -1;
    return sceLncUtilGetAppIdOfRunningBigApp();
}

/* ── Content delete (for screenshots) ────────────────────────────────── */

static void delete_init(void) {
    if (g_delete_ready) return;
    void *lib = dlopen("/system/common/lib/libSceContentDelete.sprx", RTLD_NOW);
    if (!lib) return;
    content_delete_init = dlsym(lib, "sceContentDeleteInitialize");
    content_delete_by_path = dlsym(lib, "sceContentDeleteByPath");
    content_delete_term = dlsym(lib, "sceContentDeleteTerminate");
    if (content_delete_init) {
        content_delete_param_t p = {0};
        p.buffer_size = 0x4000;
        content_delete_init(&p);
    }
    g_delete_ready = true;
}

static int delete_capture(const char *path) {
    delete_init();
    if (!content_delete_by_path) return ENOSYS;
    return content_delete_by_path(path);
}

/* ── Screenshot capture helpers ──────────────────────────────────────── */

typedef struct capture {
    char path[512];
    time_t mtime;
    off_t size;
} capture_t;

static bool is_picture(const char *name) {
    const char *ext = strrchr(name, '.');
    return ext && (!strcasecmp(ext, ".jxr") || !strcasecmp(ext, ".png") ||
                   !strcasecmp(ext, ".jpg") || !strcasecmp(ext, ".jpeg"));
}

static const char *const capture_roots[] = {"/user/av_contents/photo", "/user/av_contents"};

static void captures_walk(const char *folder, int depth, capture_t *newest, int *count) {
    DIR *dir = opendir(folder);
    if (!dir) return;
    struct dirent *ent;
    while ((ent = readdir(dir))) {
        if (ent->d_name[0] == '.') continue;
        char path[512];
        snprintf(path, sizeof(path), "%s/%s", folder, ent->d_name);
        struct stat st;
        if (stat(path, &st)) continue;
        if (S_ISDIR(st.st_mode) && depth < 3) {
            captures_walk(path, depth + 1, newest, count);
            continue;
        }
        if (!S_ISREG(st.st_mode) || !is_picture(ent->d_name)) continue;
        /* Insert sorted by mtime descending, keep NEWEST_SHOTS */
        int pos = *count < NEWEST_SHOTS ? *count : NEWEST_SHOTS;
        for (int i = 0; i < pos; i++) {
            if (st.st_mtime > newest[i].mtime) { pos = i; break; }
        }
        if (pos >= NEWEST_SHOTS) continue;
        if (*count < NEWEST_SHOTS) (*count)++;
        for (int i = *count - 1; i > pos; i--) newest[i] = newest[i - 1];
        strncpy(newest[pos].path, path, sizeof(newest[pos].path) - 1);
        newest[pos].mtime = st.st_mtime;
        newest[pos].size = st.st_size;
    }
    closedir(dir);
}

static int find_newest(capture_t *out, int *count) {
    *count = 0;
    for (size_t i = 0; i < sizeof(capture_roots) / sizeof(capture_roots[0]); i++)
        captures_walk(capture_roots[i], 0, out, count);
    return *count > 0 ? 0 : -1;
}

static const char *format_from_ext(const char *path) {
    const char *ext = strrchr(path, '.');
    if (!ext) return "unknown";
    if (!strcasecmp(ext, ".jxr")) return "jxr";
    if (!strcasecmp(ext, ".png")) return "png";
    return "jpg";
}

/* Take a screenshot by holding Create, wait for the new file, send it, delete it. */
static int cmd_shot(int fd, int hold_ms) {
    if (!g_pad.open) return reply_error(fd, "pad not open");
    capture_t before[NEWEST_SHOTS];
    int nb = 0;
    find_newest(before, &nb);
    time_t baseline = nb > 0 ? before[0].mtime : time(NULL) - 1;

    pad_hold(BUTTON_CREATE, 128, 128, 128, 128, 0, 0);
    sleep_ms(hold_ms > 0 ? (unsigned)hold_ms : DEFAULT_CREATE_HOLD_MS);
    pad_release();

    /* Wait for the new capture to appear */
    capture_t after[NEWEST_SHOTS];
    int na = 0;
    uint64_t deadline = now_ms() + SHOT_WAIT_MS;
    while (now_ms() < deadline) {
        sleep_ms(200);
        na = 0;
        find_newest(after, &na);
        if (na > 0 && after[0].mtime > baseline) break;
    }
    if (na == 0 || after[0].mtime <= baseline)
        return reply_error(fd, "no new capture appeared");

    /* Read the file */
    int ffd = open(after[0].path, O_RDONLY);
    if (ffd < 0) return reply_error(fd, "open: %s", strerror(errno));
    off_t sz = after[0].size;

    jbuf_t j;
    jb_begin(&j, 1);
    jb_int(&j, "bytes", (long long)sz);
    jb_str(&j, "path", after[0].path);
    jb_str(&j, "format", format_from_ext(after[0].path));
    int del_rc = delete_capture(after[0].path);
    jb_hex(&j, "deleted", (uint32_t)del_rc);
    if (reply_json(fd, &j)) { close(ffd); return -1; }

    /* Stream the file bytes */
    char buf[8192];
    off_t remaining = sz;
    while (remaining > 0) {
        ssize_t n = read(ffd, buf, remaining < (off_t)sizeof(buf) ? (size_t)remaining : sizeof(buf));
        if (n <= 0) break;
        if (send_all(fd, buf, (size_t)n)) break;
        remaining -= n;
    }
    close(ffd);
    return 0;
}

/* ── Filesystem commands ─────────────────────────────────────────────── */

static int cmd_fs_ls(int fd, const char *path) {
    if (!path_is_clean(path)) return reply_error(fd, "bad path");
    DIR *dir = opendir(path);
    if (!dir) return reply_error(fd, "opendir: %s", strerror(errno));
    jbuf_t j;
    jb_begin(&j, 1);
    jb_key_open(&j, "entries", '[');
    struct dirent *ent;
    while ((ent = readdir(dir))) {
        if (ent->d_name[0] == '.') continue;
        char full[512];
        snprintf(full, sizeof(full), "%s/%s", path, ent->d_name);
        struct stat st;
        if (stat(full, &st)) continue;
        jb_open(&j, '{');
        j.need_comma = 0;
        jb_str(&j, "name", ent->d_name);
        jb_str(&j, "type", S_ISDIR(st.st_mode) ? "d" : S_ISLNK(st.st_mode) ? "l" : "f");
        jb_int(&j, "size", (long long)st.st_size);
        jb_int(&j, "mtime", (long long)st.st_mtime);
        jb_close(&j, '}');
    }
    closedir(dir);
    jb_close(&j, ']');
    return reply_json(fd, &j);
}

static int cmd_fs_stat(int fd, const char *path) {
    if (!path_is_clean(path)) return reply_error(fd, "bad path");
    struct stat st;
    if (stat(path, &st)) return reply_error(fd, "stat: %s", strerror(errno));
    jbuf_t j;
    jb_begin(&j, 1);
    jb_str(&j, "type", S_ISDIR(st.st_mode) ? "d" : S_ISLNK(st.st_mode) ? "l" : "f");
    jb_int(&j, "size", (long long)st.st_size);
    jb_int(&j, "mtime", (long long)st.st_mtime);
    char mode[16];
    snprintf(mode, sizeof(mode), "%o", (unsigned)st.st_mode & 07777);
    jb_str(&j, "mode", mode);
    return reply_json(fd, &j);
}

static int cmd_fs_get(int fd, const char *path) {
    if (!path_is_clean(path)) return reply_error(fd, "bad path");
    int ffd = open(path, O_RDONLY);
    if (ffd < 0) return reply_error(fd, "open: %s", strerror(errno));
    struct stat st;
    fstat(ffd, &st);
    jbuf_t j;
    jb_begin(&j, 1);
    jb_int(&j, "bytes", (long long)st.st_size);
    if (reply_json(fd, &j)) { close(ffd); return -1; }

    char buf[8192];
    off_t rem = st.st_size;
    while (rem > 0) {
        ssize_t n = read(ffd, buf, rem < (off_t)sizeof(buf) ? (size_t)rem : sizeof(buf));
        if (n <= 0) break;
        if (send_all(fd, buf, (size_t)n)) break;
        rem -= n;
    }
    close(ffd);
    return 0;
}

static int cmd_fs_put(int fd, const char *path, off_t size, int mode) {
    if (!path_is_writable_area(path)) return reply_error(fd, "path not writable");
    char tmp[520];
    snprintf(tmp, sizeof(tmp), "%s.part", path);
    int ffd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, mode ? mode : 0644);
    if (ffd < 0) return reply_error(fd, "create: %s", strerror(errno));

    off_t written = 0;
    char buf[8192];
    while (written < size) {
        size_t want = (size_t)(size - written);
        if (want > sizeof(buf)) want = sizeof(buf);
        ssize_t n = recv(fd, buf, want, 0);
        if (n <= 0) { close(ffd); unlink(tmp); return -1; }
        if (write(ffd, buf, (size_t)n) != n) { close(ffd); unlink(tmp); return reply_error(fd, "write failed"); }
        written += n;
    }
    close(ffd);
    if (rename(tmp, path)) { unlink(tmp); return reply_error(fd, "rename: %s", strerror(errno)); }

    jbuf_t j;
    jb_begin(&j, 1);
    jb_int(&j, "written", (long long)written);
    return reply_json(fd, &j);
}

static int cmd_fs_mkdir(int fd, const char *path) {
    if (!path_is_writable_area(path)) return reply_error(fd, "path not writable");
    /* Create parents */
    char tmp[512];
    strncpy(tmp, path, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            mkdir(tmp, 0755);
            *p = '/';
        }
    }
    if (mkdir(tmp, 0755) && errno != EEXIST)
        return reply_error(fd, "mkdir: %s", strerror(errno));
    return reply_ok(fd);
}

static int cmd_fs_rm(int fd, const char *path) {
    if (!path_is_writable_area(path)) return reply_error(fd, "path not writable");
    /* Refuse dangerous paths */
    if (!strcmp(path, "/") || !strncmp(path, "/system", 7) ||
        !strncmp(path, "/preinst", 8) || !strcmp(path, "/update") ||
        !strcmp(path, "/user"))
        return reply_error(fd, "refused: system path");
    struct stat st;
    if (stat(path, &st)) return reply_error(fd, "stat: %s", strerror(errno));
    if (S_ISDIR(st.st_mode)) {
        if (rmdir(path)) return reply_error(fd, "rmdir: %s", strerror(errno));
    } else {
        if (unlink(path)) return reply_error(fd, "unlink: %s", strerror(errno));
    }
    return reply_ok(fd);
}

static int cmd_fs_df(int fd) {
    static const char *const mounts[] = {"/user", "/data", "/mnt/ext1", "/mnt/usb0", "/mnt/usb1"};
    jbuf_t j;
    jb_begin(&j, 1);
    jb_key_open(&j, "mounts", '[');
    for (size_t i = 0; i < sizeof(mounts) / sizeof(mounts[0]); i++) {
        struct statvfs sv;
        if (statvfs(mounts[i], &sv)) continue;
        jb_open(&j, '{');
        j.need_comma = 0;
        jb_str(&j, "path", mounts[i]);
        jb_int(&j, "total", (long long)(sv.f_blocks * sv.f_frsize));
        jb_int(&j, "free", (long long)(sv.f_bfree * sv.f_frsize));
        jb_close(&j, '}');
    }
    jb_close(&j, ']');
    return reply_json(fd, &j);
}

/* ── App commands ────────────────────────────────────────────────────── */

static int cmd_app_list(int fd) {
    jbuf_t j;
    jb_begin(&j, 1);
    jb_key_open(&j, "apps", '[');
    DIR *dir = opendir("/user/app");
    if (dir) {
        struct dirent *ent;
        while ((ent = readdir(dir))) {
            if (ent->d_name[0] == '.') continue;
            char param_path[512];
            snprintf(param_path, sizeof(param_path), "/user/appmeta/%s/param.json", ent->d_name);
            /* Try to read the app name from param.json — best effort */
            char name[256] = "";
            int pf = open(param_path, O_RDONLY);
            if (pf >= 0) {
                char buf[4096];
                ssize_t n = read(pf, buf, sizeof(buf) - 1);
                close(pf);
                if (n > 0) {
                    buf[n] = '\0';
                    /* Simple extraction of "titleName": "..." */
                    char *key = strstr(buf, "\"titleName\"");
                    if (!key) key = strstr(buf, "\"title\"");
                    if (key) {
                        char *colon = strchr(key + 1, ':');
                        if (colon) {
                            char *start = strchr(colon, '"');
                            if (start) {
                                start++;
                                char *end = strchr(start, '"');
                                if (end) {
                                    size_t len = (size_t)(end - start);
                                    if (len >= sizeof(name)) len = sizeof(name) - 1;
                                    memcpy(name, start, len);
                                    name[len] = '\0';
                                }
                            }
                        }
                    }
                }
            }
            jb_open(&j, '{');
            j.need_comma = 0;
            jb_str(&j, "id", ent->d_name);
            jb_str(&j, "name", name[0] ? name : ent->d_name);
            jb_close(&j, '}');
        }
        closedir(dir);
    }
    jb_close(&j, ']');
    return reply_json(fd, &j);
}

static int cmd_app_launch(int fd, const char *title_id) {
    if (!sceLncUtilLaunchApp) return reply_error(fd, "sceLncUtilLaunchApp unavailable");
    int rc = launcher_init();
    if (rc) return reply_error(fd, "launcher init: 0x%08x", (uint32_t)rc);
    int32_t user_id = g_pad.user_id;
    if (user_id <= 0) sceUserServiceGetForegroundUser(&user_id);
    const uint32_t flags[] = {0, 2, 1};
    uint32_t result = 0;
    for (unsigned i = 0; i < 3; i++) {
        launch_param_t p = {sizeof(p), (uint32_t)user_id, 0, 0, flags[i]};
        result = sceLncUtilLaunchApp(title_id, NULL, &p);
        if (result != 0x80940005u) break;
    }
    if (result & 0x80000000u) return reply_error(fd, "launch: 0x%08x", result);
    jbuf_t j;
    jb_begin(&j, 1);
    jb_int(&j, "app_id", (long long)result);
    return reply_json(fd, &j);
}

static int cmd_app_home(int fd) {
    int rc = launcher_init();
    if (rc) return reply_error(fd, "launcher init: 0x%08x", (uint32_t)rc);
    int app = running_app();
    if (app < 0) return reply_error(fd, "no running app");
    if (!sceLncUtilSuspendApp) return reply_error(fd, "sceLncUtilSuspendApp unavailable");
    rc = sceLncUtilSuspendApp(app, 0);
    if (rc) return reply_error(fd, "suspend: 0x%08x", (uint32_t)rc);
    return reply_ok(fd);
}

static int cmd_app_close(int fd) {
    int rc = launcher_init();
    if (rc) return reply_error(fd, "launcher init: 0x%08x", (uint32_t)rc);
    int app = running_app();
    if (app < 0) return reply_error(fd, "no running app");
    if (!sceLncUtilKillApp) return reply_error(fd, "sceLncUtilKillApp unavailable");
    rc = sceLncUtilKillApp(app);
    if (rc) return reply_error(fd, "kill: 0x%08x", (uint32_t)rc);
    return reply_ok(fd);
}

static int cmd_app_running(int fd) {
    jbuf_t j;
    jb_begin(&j, 1);
    jb_int(&j, "app_id", (long long)running_app());
    return reply_json(fd, &j);
}

/* ── Sys commands ────────────────────────────────────────────────────── */

static int cmd_sys_info(int fd) {
    jbuf_t j;
    jb_begin(&j, 1);
    /* Firmware version from the SDK kernel helper if available */
    uint32_t fw = 0;
    int sdk_fd = open("/system/common/lib/libkernel.sprx", O_RDONLY);
    if (sdk_fd >= 0) close(sdk_fd);
    jb_hex(&j, "fw", fw);
    struct timespec up;
    if (clock_gettime(CLOCK_MONOTONIC, &up) == 0)
        jb_int(&j, "uptime_s", (long long)up.tv_sec);
    return reply_json(fd, &j);
}

/* ── Command dispatch ────────────────────────────────────────────────── */

static int handle_command(int fd, char *line) {
    /* Tokenize: up to 16 words */
    char *words[16];
    int nw = 0;
    char *save = NULL;
    for (char *tok = strtok_r(line, " ", &save); tok && nw < 16; tok = strtok_r(NULL, " ", &save))
        words[nw++] = tok;
    if (nw == 0) return reply_error(fd, "empty command");

    /* hello */
    if (!strcmp(words[0], "hello")) {
        jbuf_t j;
        jb_begin(&j, 1);
        jb_str(&j, "name", JOKER_NAME);
        jb_str(&j, "version", JOKER_VERSION);
        jb_int(&j, "proto", JOKER_PROTO);
        jb_hex(&j, "fw", 0);
        jb_int(&j, "pid", (long long)getpid());
        jb_str(&j, "pad", g_pad.open ? "open" : "closed");
        return reply_json(fd, &j);
    }

    /* pad ... */
    if (!strcmp(words[0], "pad") && nw >= 2) {
        if (!strcmp(words[1], "open")) {
            int32_t uid = nw >= 3 ? (int32_t)strtol(words[2], NULL, 0) : 0;
            char steps[2048] = "";
            int rc = cmd_pad_open(uid, steps, sizeof(steps));
            jbuf_t j;
            jb_begin(&j, rc == 0);
            if (rc) jb_str(&j, "error", "pad open failed");
            jb_str(&j, "backend", "hid");
            jb_int(&j, "handle", (long long)g_pad.handle);
            jb_int(&j, "user", (long long)g_pad.user_id);
            jb_steps(&j, "steps", steps);
            return reply_json(fd, &j);
        }
        if (!strcmp(words[1], "close")) {
            char steps[1024] = "";
            cmd_pad_close(steps, sizeof(steps));
            jbuf_t j;
            jb_begin(&j, 1);
            jb_steps(&j, "steps", steps);
            return reply_json(fd, &j);
        }
        if (!strcmp(words[1], "status")) {
            jbuf_t j;
            jb_begin(&j, 1);
            jb_bool(&j, "open", g_pad.open);
            jb_str(&j, "backend", "hid");
            jb_int(&j, "handle", (long long)g_pad.handle);
            jb_int(&j, "user", (long long)g_pad.user_id);
            jb_int(&j, "inserts", (long long)g_pad.inserts);
            jb_hex(&j, "last", (uint32_t)g_pad.last_insert);
            return reply_json(fd, &j);
        }
        if (!strcmp(words[1], "set") && nw >= 9) {
            uint32_t btns = (uint32_t)strtoul(words[2], NULL, 16);
            int lx = atoi(words[3]), ly = atoi(words[4]);
            int rx = atoi(words[5]), ry = atoi(words[6]);
            int l2 = atoi(words[7]), r2 = atoi(words[8]);
            pad_hold(btns, lx, ly, rx, ry, l2, r2);
            return reply_ok(fd);
        }
        if (!strcmp(words[1], "press") && nw >= 4) {
            if (!g_pad.open) return reply_error(fd, "pad not open");
            uint32_t btns = (uint32_t)strtoul(words[2], NULL, 16);
            int ms = atoi(words[3]);
            if (ms < 1 || ms > 10000) return reply_error(fd, "ms out of range");
            pad_hold(btns, 128, 128, 128, 128, 0, 0);
            sleep_ms((unsigned)ms);
            pad_release();
            sleep_ms(50);
            return reply_ok(fd);
        }
        if (!strcmp(words[1], "seq") && nw >= 3) {
            if (!g_pad.open) return reply_error(fd, "pad not open");
            int steps_run = 0;
            for (int i = 2; i < nw && steps_run < MAX_SEQ_STEPS; i++) {
                if (words[i][0] == 'w') {
                    int ms = atoi(words[i] + 1);
                    if (ms > 0 && ms <= 10000) sleep_ms((unsigned)ms);
                } else {
                    char *colon = strchr(words[i], ':');
                    if (!colon) continue;
                    *colon = '\0';
                    uint32_t btns = (uint32_t)strtoul(words[i], NULL, 16);
                    int ms = atoi(colon + 1);
                    if (ms < 1 || ms > 10000) continue;
                    pad_hold(btns, 128, 128, 128, 128, 0, 0);
                    sleep_ms((unsigned)ms);
                    pad_release();
                    sleep_ms(50);
                }
                steps_run++;
            }
            jbuf_t j;
            jb_begin(&j, 1);
            jb_int(&j, "steps_run", (long long)steps_run);
            return reply_json(fd, &j);
        }
        if (!strcmp(words[1], "release")) {
            pad_release();
            return reply_ok(fd);
        }
    }

    /* shot / shots */
    if (!strcmp(words[0], "shot"))
        return cmd_shot(fd, nw >= 2 ? atoi(words[1]) : 0);
    if (!strcmp(words[0], "shots")) {
        capture_t caps[NEWEST_SHOTS];
        int n = 0;
        find_newest(caps, &n);
        jbuf_t j;
        jb_begin(&j, 1);
        jb_key_open(&j, "files", '[');
        for (int i = 0; i < n; i++) {
            jb_open(&j, '{');
            j.need_comma = 0;
            jb_str(&j, "path", caps[i].path);
            jb_int(&j, "size", (long long)caps[i].size);
            jb_int(&j, "mtime", (long long)caps[i].mtime);
            jb_close(&j, '}');
        }
        jb_close(&j, ']');
        return reply_json(fd, &j);
    }

    /* app ... */
    if (!strcmp(words[0], "app") && nw >= 2) {
        if (!strcmp(words[1], "list")) return cmd_app_list(fd);
        if (!strcmp(words[1], "launch") && nw >= 3) return cmd_app_launch(fd, words[2]);
        if (!strcmp(words[1], "home")) return cmd_app_home(fd);
        if (!strcmp(words[1], "close")) return cmd_app_close(fd);
        if (!strcmp(words[1], "running")) return cmd_app_running(fd);
    }

    /* fs ... */
    if (!strcmp(words[0], "fs") && nw >= 2) {
        if (!strcmp(words[1], "ls") && nw >= 3) return cmd_fs_ls(fd, words[2]);
        if (!strcmp(words[1], "stat") && nw >= 3) return cmd_fs_stat(fd, words[2]);
        if (!strcmp(words[1], "get") && nw >= 3) return cmd_fs_get(fd, words[2]);
        if (!strcmp(words[1], "put") && nw >= 4) {
            off_t sz = (off_t)strtoll(words[3], NULL, 10);
            int mode = nw >= 5 ? (int)strtol(words[4], NULL, 8) : 0;
            return cmd_fs_put(fd, words[2], sz, mode);
        }
        if (!strcmp(words[1], "mkdir") && nw >= 3) return cmd_fs_mkdir(fd, words[2]);
        if (!strcmp(words[1], "rm") && nw >= 3) return cmd_fs_rm(fd, words[2]);
        if (!strcmp(words[1], "df")) return cmd_fs_df(fd);
    }

    /* sys ... */
    if (!strcmp(words[0], "sys") && nw >= 2) {
        if (!strcmp(words[1], "notify") && nw >= 3) {
            /* Rejoin the rest of the line for the notification text */
            char text[512] = "";
            for (int i = 2; i < nw; i++) {
                if (i > 2) strncat(text, " ", sizeof(text) - strlen(text) - 1);
                strncat(text, words[i], sizeof(text) - strlen(text) - 1);
            }
            notify("%s", text);
            return reply_ok(fd);
        }
        if (!strcmp(words[1], "info")) return cmd_sys_info(fd);
    }

    /* quit */
    if (!strcmp(words[0], "quit")) {
        char steps[256] = "";
        cmd_pad_close(steps, sizeof(steps));
        reply_ok(fd);
        return 1; /* signal to exit */
    }

    return reply_error(fd, "unknown command: %s", words[0]);
}

/* ── Client handler ──────────────────────────────────────────────────── */

static void handle_client(int fd) {
    char buf[LINE_MAX_LEN + 1];
    size_t used = 0;
    while (1) {
        ssize_t n = recv(fd, buf + used, sizeof(buf) - 1 - used, 0);
        if (n <= 0) break;
        used += (size_t)n;
        buf[used] = '\0';
        char *nl;
        while ((nl = strchr(buf, '\n'))) {
            *nl = '\0';
            /* Strip trailing \r */
            if (nl > buf && *(nl - 1) == '\r') *(nl - 1) = '\0';
            joker_log("jokerd: <- %s", buf);
            int rc = handle_command(fd, buf);
            if (rc == 1) return; /* quit */
            /* Shift the buffer */
            size_t consumed = (size_t)(nl - buf) + 1;
            used -= consumed;
            memmove(buf, nl + 1, used);
            buf[used] = '\0';
        }
        if (used >= LINE_MAX_LEN) {
            reply_error(fd, "line too long");
            used = 0;
        }
    }
    /* Client disconnected — release the pad to neutral */
    if (g_pad.open) pad_release();
}

/* ── Main ────────────────────────────────────────────────────────────── */

int main(void) {
    signal(SIGPIPE, SIG_IGN);
    joker_log("jokerd: %s v%s starting on port %d", JOKER_NAME, JOKER_VERSION, JOKER_PORT);
    notify("Joker v%s — port %d", JOKER_VERSION, JOKER_PORT);

    /* Initialize user service early */
    sceUserServiceInitialize(NULL);

    /* Start the insert thread */
    pthread_t tid;
    pthread_create(&tid, NULL, insert_thread, NULL);
    pthread_detach(tid);

    /* Listen */
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) { joker_log("jokerd: socket: %s", strerror(errno)); return 1; }
    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(JOKER_PORT);
    if (bind(srv, (struct sockaddr *)&addr, sizeof(addr))) {
        joker_log("jokerd: bind: %s", strerror(errno));
        close(srv);
        return 1;
    }
    listen(srv, 2);
    joker_log("jokerd: listening");

    for (;;) {
        struct sockaddr_in client_addr;
        socklen_t client_len = sizeof(client_addr);
        int fd = accept(srv, (struct sockaddr *)&client_addr, &client_len);
        if (fd < 0) continue;
        joker_log("jokerd: client from %s", inet_ntoa(client_addr.sin_addr));
        handle_client(fd);
        close(fd);
        joker_log("jokerd: client disconnected");
    }
}
