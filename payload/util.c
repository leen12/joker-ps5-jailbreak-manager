/* Joker PS5 Control — shared helpers.
 * SPDX-License-Identifier: MIT */

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include <ps5/kernel.h>
#include "util.h"

/* ── Logging ─────────────────────────────────────────────────────────── */

void joker_log(const char *fmt, ...) {
    va_list ap, cp;
    va_start(ap, fmt);
    va_copy(cp, ap);
    vprintf(fmt, ap);
    printf("\n");
    fflush(stdout);
    FILE *f = fopen(JOKER_LOG, "a");
    if (f) { vfprintf(f, fmt, cp); fputc('\n', f); fclose(f); }
    va_end(cp);
    va_end(ap);
}

/* ── Time ────────────────────────────────────────────────────────────── */

uint64_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

void sleep_ms(unsigned ms) {
    usleep((useconds_t)ms * 1000u);
}

/* ── Privilege escalation ────────────────────────────────────────────── */

#define JOKER_AUTHID 0x4800000000010003ull

void elevate(char *steps, size_t size) {
    static const uint8_t all_caps[16] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
                                         0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    int authid = kernel_set_ucred_authid(getpid(), JOKER_AUTHID);
    int caps = kernel_set_ucred_caps(getpid(), all_caps);
    step(steps, size, "authid=%d caps=%d", authid, caps);
}

/* ── Step log ────────────────────────────────────────────────────────── */

void step(char *steps, size_t size, const char *fmt, ...) {
    size_t used = strlen(steps);
    if (used + 2 >= size) return;
    if (used > 0) steps[used++] = ' ';
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(steps + used, size - used, fmt, ap);
    va_end(ap);
}

/* ── JSON builder ────────────────────────────────────────────────────── */

static void jb_raw(jbuf_t *j, const char *s, size_t n) {
    if (j->overflow) return;
    if (j->len + n >= sizeof(j->data) - 2) { j->overflow = 1; return; }
    memcpy(j->data + j->len, s, n);
    j->len += n;
}

static void jb_comma(jbuf_t *j) {
    if (j->need_comma) jb_raw(j, ",", 1);
    j->need_comma = 1;
}

static void jb_escaped(jbuf_t *j, const char *s) {
    jb_raw(j, "\"", 1);
    for (; *s && !j->overflow; s++) {
        switch (*s) {
        case '"':  jb_raw(j, "\\\"", 2); break;
        case '\\': jb_raw(j, "\\\\", 2); break;
        case '\n': jb_raw(j, "\\n", 2); break;
        case '\r': jb_raw(j, "\\r", 2); break;
        case '\t': jb_raw(j, "\\t", 2); break;
        default:   jb_raw(j, s, 1); break;
        }
    }
    jb_raw(j, "\"", 1);
}

void jb_begin(jbuf_t *j, int ok) {
    memset(j, 0, sizeof(*j));
    jb_raw(j, "{", 1);
    j->need_comma = 0;
    jb_bool(j, "ok", ok);
}

void jb_str(jbuf_t *j, const char *key, const char *value) {
    jb_comma(j);
    jb_escaped(j, key);
    jb_raw(j, ":", 1);
    jb_escaped(j, value);
}

void jb_int(jbuf_t *j, const char *key, long long value) {
    jb_comma(j);
    jb_escaped(j, key);
    char buf[32];
    int n = snprintf(buf, sizeof(buf), ":%lld", value);
    jb_raw(j, buf, (size_t)n);
}

void jb_hex(jbuf_t *j, const char *key, uint32_t value) {
    jb_comma(j);
    jb_escaped(j, key);
    char buf[16];
    int n = snprintf(buf, sizeof(buf), ":\"0x%08x\"", value);
    jb_raw(j, buf, (size_t)n);
}

void jb_bool(jbuf_t *j, const char *key, int value) {
    jb_comma(j);
    jb_escaped(j, key);
    jb_raw(j, value ? ":true" : ":false", value ? 5 : 6);
}

void jb_key_open(jbuf_t *j, const char *key, char bracket) {
    jb_comma(j);
    jb_escaped(j, key);
    jb_raw(j, ":", 1);
    jb_raw(j, &bracket, 1);
    j->need_comma = 0;
}

void jb_open(jbuf_t *j, char bracket) {
    jb_comma(j);
    jb_raw(j, &bracket, 1);
    j->need_comma = 0;
}

void jb_close(jbuf_t *j, char bracket) {
    jb_raw(j, &bracket, 1);
    j->need_comma = 1;
}

void jb_elem_str(jbuf_t *j, const char *value) {
    jb_comma(j);
    jb_escaped(j, value);
}

void jb_end(jbuf_t *j) {
    jb_raw(j, "}\n", 2);
    j->data[j->len] = '\0';
}

void jb_steps(jbuf_t *j, const char *key, const char *steps) {
    jb_key_open(j, key, '[');
    char buf[2048];
    strncpy(buf, steps, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';
    char *save = NULL;
    for (char *tok = strtok_r(buf, " ", &save); tok; tok = strtok_r(NULL, " ", &save))
        jb_elem_str(j, tok);
    jb_close(j, ']');
}

/* ── Socket helpers ──────────────────────────────────────────────────── */

int send_all(int fd, const void *buf, size_t len) {
    const uint8_t *p = (const uint8_t *)buf;
    while (len > 0) {
        ssize_t n = send(fd, p, len, 0);
        if (n <= 0) return -1;
        p += n;
        len -= (size_t)n;
    }
    return 0;
}

int recv_all(int fd, void *buf, size_t len) {
    uint8_t *p = (uint8_t *)buf;
    while (len > 0) {
        ssize_t n = recv(fd, p, len, 0);
        if (n <= 0) return -1;
        p += n;
        len -= (size_t)n;
    }
    return 0;
}

int reply_json(int fd, jbuf_t *j) {
    jb_end(j);
    return send_all(fd, j->data, j->len);
}

int reply_error(int fd, const char *fmt, ...) {
    jbuf_t j;
    jb_begin(&j, 0);
    char msg[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    jb_str(&j, "error", msg);
    return reply_json(fd, &j);
}

int reply_ok(int fd) {
    jbuf_t j;
    jb_begin(&j, 1);
    return reply_json(fd, &j);
}

/* ── Path validation ─────────────────────────────────────────────────── */

int path_is_clean(const char *path) {
    if (!path || path[0] != '/') return 0;
    if (strstr(path, "..")) return 0;
    if (strstr(path, "//")) return 0;
    return 1;
}

int path_is_writable_area(const char *path) {
    if (!path_is_clean(path)) return 0;
    static const char *const allowed[] = {
        "/data/", "/user/data/", "/user/av_contents/", "/user/app/",
        "/user/homebrew/", "/mnt/ext1/", "/mnt/usb0/", "/mnt/usb1/",
        NULL
    };
    for (const char *const *p = allowed; *p; p++)
        if (strncmp(path, *p, strlen(*p)) == 0) return 1;
    return 0;
}

/* ── PS5 notification ────────────────────────────────────────────────── */

int sceKernelSendNotificationRequest(int device, const void *buf, size_t size, int blocking)
    __attribute__((weak));

typedef struct notify_request {
    int type;
    int req_id;
    int priority;
    int msg_id;
    int target_id;
    int user_id;
    int unk1;
    int unk2;
    int app_id;
    int error_num;
    int unk3;
    char use_icon_image_uri;
    char message[1024];
    char uri[1024];
    char unkstr[1024];
} notify_request_t;

void notify(const char *fmt, ...) {
    if (!sceKernelSendNotificationRequest) return;
    notify_request_t req;
    memset(&req, 0, sizeof(req));
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(req.message, sizeof(req.message), fmt, ap);
    va_end(ap);
    sceKernelSendNotificationRequest(0, &req, sizeof(req), 0);
}
