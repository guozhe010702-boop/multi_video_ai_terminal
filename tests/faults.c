/* Test-only syscall and RKNN error injection. Loaded explicitly by tests. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int reject_snapshot(const char* filename, const char* mode) {
    const char* marker = getenv("MVAI_FAIL_SNAPSHOT_FILE");
    return marker && access(marker, F_OK) == 0 && strchr(mode, 'w') &&
           strstr(filename, "/snapshots/") && strstr(filename, ".jpg.tmp");
}
FILE* fopen(const char* filename, const char* mode) {
    if (reject_snapshot(filename, mode)) { errno = ENOSPC; return NULL; }
    FILE* (*original)(const char*, const char*) = dlsym(RTLD_NEXT, "fopen");
    return original(filename, mode);
}
FILE* fopen64(const char* filename, const char* mode) {
    if (reject_snapshot(filename, mode)) { errno = ENOSPC; return NULL; }
    FILE* (*original)(const char*, const char*) = dlsym(RTLD_NEXT, "fopen64");
    return original(filename, mode);
}
int rknn_run(uint64_t context, void* extend) {
    const char* hang = getenv("MVAI_HANG_INFERENCE_FILE");
    if (hang && unlink(hang) == 0) sleep(20);
    const char* marker = getenv("MVAI_FAIL_INFERENCE_FILE");
    if (marker && unlink(marker) == 0) return -1;
    int (*original)(uint64_t, void*) = dlsym(RTLD_NEXT, "rknn_run");
    return original(context, extend);
}
