#pragma once
#include <stdint.h>
#include <stddef.h>
struct mbedtls_sha256_context { int x; };
inline void mbedtls_sha256_init(mbedtls_sha256_context *) {}
inline int mbedtls_sha256_starts(mbedtls_sha256_context *, int) { return 0; }
inline int mbedtls_sha256_update(mbedtls_sha256_context *, const unsigned char *, size_t) { return 0; }
inline int mbedtls_sha256_finish(mbedtls_sha256_context *, unsigned char *out) { for (int i = 0; i < 32; i++) out[i] = 0; return 0; }
inline void mbedtls_sha256_free(mbedtls_sha256_context *) {}
