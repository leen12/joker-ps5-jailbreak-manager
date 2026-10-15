/* Joker cooperative frame-capture integration.
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

typedef int (*joker_capture_writer)(const char *output_path, void *context);

/*
 * Call periodically from the title's frame loop.
 *
 * Returns 0 when no request exists, 1 after writing a frame, or a negative
 * errno-style value. unlink(request_path) is the atomic claim operation, so
 * only one frame loop invocation handles each request.
 */
static inline int joker_capture_poll(
    const char *request_path,
    const char *output_folder,
    const char *extension,
    joker_capture_writer writer,
    void *context)
{
    if (request_path == NULL || output_folder == NULL || extension == NULL || writer == NULL)
        return -EINVAL;
    if (strchr(extension, '/') != NULL || strchr(extension, '.') != NULL)
        return -EINVAL;
    if (unlink(request_path) != 0)
        return errno == ENOENT ? 0 : -errno;
    if (mkdir(output_folder, 0777) != 0 && errno != EEXIST)
        return -errno;

    struct timespec now;
    if (clock_gettime(CLOCK_REALTIME, &now) != 0)
        return -errno;
    char output_path[512];
    int count = snprintf(output_path, sizeof(output_path), "%s/joker-%lld-%09ld-%d.%s",
                         output_folder, (long long)now.tv_sec, now.tv_nsec, (int)getpid(), extension);
    if (count < 0 || (size_t)count >= sizeof(output_path))
        return -ENAMETOOLONG;

    int result = writer(output_path, context);
    if (result != 0) {
        unlink(output_path);
        return result < 0 ? result : -EIO;
    }
    if (chmod(output_path, 0666) != 0) {
        int error = errno;
        unlink(output_path);
        return -error;
    }
    return 1;
}
