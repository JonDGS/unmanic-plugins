#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone test harness for the keep_original_language_audio plugin.

Runs OUTSIDE Unmanic by stubbing the `unmanic` package and mocking ffprobe /
arr HTTP calls, so the pure decision logic can be exercised in CI or locally:

    python3 test_plugin.py

Exits non-zero on any failure.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
# Put the parent dir (the "plugins directory") on the path, mirroring Unmanic,
# so `import keep_original_language_audio.plugin` works.
PLUGINS_DIR = os.path.dirname(HERE)
sys.path.insert(0, PLUGINS_DIR)

# --- Stub the unmanic package so plugin.py imports cleanly ------------------
unmanic = types.ModuleType("unmanic")
libs = types.ModuleType("unmanic.libs")
unplugins = types.ModuleType("unmanic.libs.unplugins")
settings_mod = types.ModuleType("unmanic.libs.unplugins.settings")


class PluginSettings(object):
    def __init__(self, *args, **kwargs):
        self._values = dict(getattr(self, "settings", {}))

    def get_setting(self, key=None):
        if key is None:
            return self._values
        return self._values.get(key)

    def set_setting(self, key, value):
        self._values[key] = value


settings_mod.PluginSettings = PluginSettings
unplugins.settings = settings_mod
libs.unplugins = unplugins
unmanic.libs = libs
sys.modules["unmanic"] = unmanic
sys.modules["unmanic.libs"] = libs
sys.modules["unmanic.libs.unplugins"] = unplugins
sys.modules["unmanic.libs.unplugins.settings"] = settings_mod

from keep_original_language_audio import plugin  # noqa: E402

# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------
_failures = []


def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        _failures.append(name)


def make_streams(audio_langs):
    """Build a fake ffprobe stream list: one video + audio streams by language.

    audio_langs is a list of (language, title) tuples; title may be None.
    """
    streams = [{"codec_type": "video"}]
    for lang, title in audio_langs:
        tags = {}
        if lang is not None:
            tags["language"] = lang
        if title:
            tags["title"] = title
        streams.append({"codec_type": "audio", "tags": tags})
    return streams


def new_settings(**overrides):
    s = plugin.Settings()
    for k, v in overrides.items():
        s.set_setting(k, v)
    return s


# ---------------------------------------------------------------------------
# language_map tests
# ---------------------------------------------------------------------------
from keep_original_language_audio.lib.language_map import (  # noqa: E402
    to_iso639_2, stream_language, normalise_keep_list,
)

check("to_iso639_2 name", to_iso639_2("Spanish") == "spa")
check("to_iso639_2 2-letter", to_iso639_2("ja") == "jpn")
check("to_iso639_2 3-letter T-variant", to_iso639_2("deu") == "ger")
check("to_iso639_2 undefined", to_iso639_2("und") is None)
check("to_iso639_2 unknown", to_iso639_2("klingon") is None)
check("stream_language tagged", stream_language({"language": "jpn"}) == "jpn")
check("stream_language missing", stream_language({}) == "und")
check("normalise_keep_list mixed",
      normalise_keep_list("eng, Spanish, xx") == {"eng", "spa"})


# ---------------------------------------------------------------------------
# _plan_audio / runner tests (mock ffprobe + arr lookup)
# ---------------------------------------------------------------------------
def run_case(name, audio_langs, original_code, settings, expect_keep, expect_remove):
    plugin._ffprobe_streams = lambda p, s=make_streams(audio_langs): s
    plugin._resolve_original_language = lambda p, s: (original_code, "test")
    plan = plugin._plan_audio("/lib/file.mkv", settings)
    ok = (plan["keep_audio_idx"] == expect_keep and plan["remove_count"] == expect_remove)
    if not ok:
        print("   got keep={} remove={} reason={}".format(
            plan["keep_audio_idx"], plan["remove_count"], plan["reason"]))
    check(name, ok)


# Original jpn, file has eng+jpn -> keep jpn (idx 1), remove eng
run_case("keep original only",
         [("eng", None), ("jpn", None)], "jpn",
         new_settings(), [1], 1)

# Original jpn + keep-list eng -> keep both
run_case("keep original + extra",
         [("eng", None), ("jpn", None)], "jpn",
         new_settings(additional_languages="eng"), [0, 1], 0)

# Only jpn present, original jpn -> nothing removed
run_case("single track no-op",
         [("jpn", None)], "jpn",
         new_settings(), [0], 0)

# Unresolved original + empty keep-list -> keep all (fail safe)
run_case("unresolved keeps all",
         [("eng", None), ("jpn", None)], None,
         new_settings(), [0, 1], 0)

# Rule would strip all -> keep all (fail safe). Original fra, file has eng+jpn.
run_case("never strip all audio",
         [("eng", None), ("jpn", None)], "fre",
         new_settings(keep_undefined=False), [0, 1], 0)

# Undefined track kept by default
run_case("keep undefined default",
         [(None, None), ("jpn", None)], "jpn",
         new_settings(), [0, 1], 0)

# Undefined track dropped when keep_undefined off (original jpn present so audio remains)
run_case("drop undefined when disabled",
         [(None, None), ("jpn", None)], "jpn",
         new_settings(keep_undefined=False), [1], 1)

# Commentary eng track dropped when original jpn and commentary off
run_case("drop commentary",
         [("jpn", None), ("eng", "Director Commentary")], "jpn",
         new_settings(), [0], 1)


# ---------------------------------------------------------------------------
# on_worker_process command build
# ---------------------------------------------------------------------------
def test_worker_command():
    plugin._ffprobe_streams = lambda p: make_streams([("eng", None), ("jpn", None)])
    plugin._resolve_original_language = lambda p, s: ("jpn", "test")
    data = {
        "library_id": 1,
        "file_in": "/cache/file.mkv",
        "file_out": "/cache/file-out.mkv",
        "original_file_path": "/lib/file.mkv",
        "exec_command": [],
        "repeat": True,
    }
    out = plugin.on_worker_process(data)
    cmd = out["exec_command"]
    expected_tail = ["-map", "0:a:1", "-c", "copy", "-y", "/cache/file-out.mkv"]
    ok = (cmd[:1] == ["ffmpeg"] and cmd[-len(expected_tail):] == expected_tail
          and "0:a:0" not in cmd)
    if not ok:
        print("   got:", cmd)
    check("worker builds ffmpeg map keeping only jpn", ok)


test_worker_command()

print("")
if _failures:
    print("{} FAILURE(S): {}".format(len(_failures), ", ".join(_failures)))
    sys.exit(1)
print("ALL TESTS PASSED")
