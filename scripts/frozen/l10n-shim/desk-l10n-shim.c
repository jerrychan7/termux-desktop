/*
 * Minimal gettext shim for the Termux desktop experiment.
 *
 * FROZEN (2026-11-13): kept as a starting point for later work. It is not
 * wired into desk, install.sh or the menus. See README.md in this directory.
 *
 * Termux/Android has no libintl: the NDK's <libintl.h> turns gettext()
 * into an inline stub that returns msgid, and GLib is built against it,
 * so g_dgettext() always returns msgid.  This shim interposes g_dgettext()
 * and reads translations directly from .mo files at
 *
 *   ${DESK_L10N_DIR:-$PREFIX/share/locale}/<DESK_L10N_LANG>/LC_MESSAGES/<domain>.mo
 *
 * It is an experiment, not a general libintl replacement: no contexts,
 * no plurals, no encoding conversion.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

typedef struct {
    char *domain;
    unsigned char *data;
    size_t size;
} entry_t;

static entry_t *cache = NULL;
static size_t cache_n = 0;
static void log_lookup(const char *kind, const char *domain,
                       const char *context, const char *msgid,
                       const char *result);

static const char *env_or(const char *key, const char *fallback)
{
    const char *value = getenv(key);
    return (value && *value) ? value : fallback;
}

static const char *lang(void)
{
    return env_or("DESK_L10N_LANG", "zh_CN");
}

static const char *dir(void)
{
    return env_or("DESK_L10N_DIR",
                  "/data/data/com.termux/files/usr/share/locale");
}

static uint32_t rd32(const unsigned char *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static entry_t *load_domain(const char *domain)
{
    size_t i;
    char path[4096];
    FILE *file;
    long size;
    entry_t entry;
    entry_t *grown;

    for (i = 0; i < cache_n; i++) {
        if (strcmp(cache[i].domain, domain) == 0) {
            return &cache[i];
        }
    }

    entry.domain = strdup(domain);
    entry.data = NULL;
    entry.size = 0;

    snprintf(path, sizeof(path), "%s/%s/LC_MESSAGES/%s.mo",
             dir(), lang(), domain);
    file = fopen(path, "rb");
    if (file != NULL) {
        if (fseek(file, 0, SEEK_END) == 0 &&
            (size = ftell(file)) > 0 &&
            fseek(file, 0, SEEK_SET) == 0) {
            entry.data = malloc((size_t)size);
            if (entry.data != NULL &&
                fread(entry.data, 1, (size_t)size, file) != (size_t)size) {
                free(entry.data);
                entry.data = NULL;
            } else {
                entry.size = (size_t)size;
            }
        }
        fclose(file);
    }

    grown = realloc(cache, (cache_n + 1) * sizeof(*cache));
    if (grown == NULL) {
        free(entry.domain);
        free(entry.data);
        return NULL;
    }
    cache = grown;
    cache[cache_n] = entry;
    return &cache[cache_n++];
}

static const char *lookup(entry_t *entry, const char *msgid)
{
    const unsigned char *data;
    size_t n, originals, translations, msgid_len, i;

    if (entry == NULL || entry->data == NULL || entry->size < 28) {
        return NULL;
    }
    data = entry->data;
    if (rd32(data) != 0x950412deu) {
        return NULL;
    }

    n = rd32(data + 8);
    originals = rd32(data + 12);
    translations = rd32(data + 16);
    msgid_len = strlen(msgid);

    if (originals + n * 8 > entry->size ||
        translations + n * 8 > entry->size) {
        return NULL;
    }

    for (i = 0; i < n; i++) {
        size_t original_len = rd32(data + originals + i * 8);
        size_t original_off = rd32(data + originals + i * 8 + 4);
        size_t translation_len, translation_off;

        if (original_len != msgid_len ||
            original_off + original_len > entry->size) {
            continue;
        }
        if (memcmp(data + original_off, msgid, msgid_len) != 0) {
            continue;
        }

        translation_len = rd32(data + translations + i * 8);
        translation_off = rd32(data + translations + i * 8 + 4);
        if (translation_len == 0 ||
            translation_off + translation_len >= entry->size) {
            return msgid;
        }
        return (const char *)(data + translation_off);
    }
    return NULL;
}

const char *g_dgettext(const char *domain, const char *msgid)
{
    const char *translated;

    if (domain == NULL || msgid == NULL || *msgid == '\0') {
        return msgid;
    }
    translated = lookup(load_domain(domain), msgid);
    log_lookup("dgettext", domain, NULL, msgid,
               translated != NULL ? translated : msgid);
    return translated != NULL ? translated : msgid;
}

const char *g_dcgettext(const char *domain, const char *msgid, int category)
{
    (void)category;
    return g_dgettext(domain, msgid);
}

const char *g_dpgettext2(const char *domain, const char *context,
                         const char *msgid)
{
    size_t context_len;
    char *prefixed;
    const char *translated;

    if (domain == NULL || msgid == NULL) {
        return msgid;
    }
    if (context == NULL) {
        return g_dgettext(domain, msgid);
    }

    context_len = strlen(context);
    prefixed = malloc(context_len + strlen(msgid) + 2);
    if (prefixed == NULL) {
        return msgid;
    }
    memcpy(prefixed, context, context_len);
    prefixed[context_len] = '\x04';
    strcpy(prefixed + context_len + 1, msgid);

    translated = lookup(load_domain(domain), prefixed);
    free(prefixed);
    if (translated != NULL) {
        log_lookup("dpgettext2", domain, context, msgid, translated);
        return translated;
    }
    log_lookup("dpgettext2", domain, context, msgid, msgid);
    return msgid;
}

static void log_lookup(const char *kind, const char *domain,
                       const char *context, const char *msgid,
                       const char *result)
{
    if (getenv("DESK_L10N_LOG") == NULL) {
        return;
    }
    if (context != NULL) {
        fprintf(stderr, "%s [%s|%s] %s -> %s\n", kind, domain, context,
                msgid, result);
    } else {
        fprintf(stderr, "%s [%s] %s -> %s\n", kind, domain, msgid, result);
    }
}
