/* Joker PS5 Control - shared helpers.
 * SPDX-License-Identifier: MIT */
#pragma once

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#define JOKER_NAME "jokerd"
#define JOKER_VERSION "0.1.0"
#define JOKER_PROTO 1
#define JOKER_PORT 9310
#define JOKER_LOG "/data/jokerd.log"

void joker_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
uint64_t now_ms(void);
void sleep_ms(unsigned ms);

/* Raise this process to the credentials system services accept from a payload. */
void elevate(char *steps, size_t size);

/* Append "label=value" to a space-separated step log (bounded, never overflows). */
void step(char *steps, size_t size, const char *fmt, ...) __attribute__((format(printf, 3, 4)));

/* Minimal JSON writer for one reply line. Overlong output is truncated but stays valid JSON. */
typedef struct jbuf {
    char data[16384];
    size_t len;
    int need_comma;
    int overflow;
} jbuf_t;

void jb_begin(jbuf_t *j, int ok);
void jb_str(jbuf_t *j, const char *key, const char *value);
void jb_int(jbuf_t *j, const char *key, long long value);
void jb_hex(jbuf_t *j, const char *key, uint32_t value);
void jb_bool(jbuf_t *j, const char *key, int value);
void jb_key_open(jbuf_t *j, const char *key, char bracket);
void jb_open(jbuf_t *j, char bracket);
void jb_close(jbuf_t *j, char bracket);
void jb_elem_str(jbuf_t *j, const char *value);
void jb_end(jbuf_t *j);

/* Steps as a JSON array of strings, split on spaces. */
void jb_steps(jbuf_t *j, const char *key, const char *steps);

/* Socket helpers. */
int send_all(int fd, const void *buf, size_t len);
int recv_all(int fd, void *buf, size_t len);
int reply_json(int fd, jbuf_t *j);
int reply_error(int fd, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
int reply_ok(int fd);

/* Path rules shared by fs commands. */
int path_is_clean(const char *path);
int path_is_writable_area(const char *path);

/* PS5 notification popup. */
void notify(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
