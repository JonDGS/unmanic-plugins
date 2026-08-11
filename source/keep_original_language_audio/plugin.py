#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    keep_original_language_audio.plugin.py

    Unmanic plugin: remove audio tracks that are not the item's original
    language (looked up from Sonarr/Radarr), with an optional user keep-list.

    Written by: Alfred (for Jon)
    License:    MIT
"""

import json
import logging
import os
import subprocess

from unmanic.libs.unplugins.settings import PluginSettings

# The plugin's bundled lib/ directory (and CI-built site-packages) are added to
# sys.path by Unmanic at load time, so these imports resolve at runtime.
from keep_original_language_audio.lib.language_map import (
    normalise_keep_list,
    stream_language,
    to_iso639_2,
)

try:
    import requests
except ImportError:  # pragma: no cover - requests is bundled via requirements.txt
    requests = None

# Unmanic logs under the "Unmanic.Plugin.<id>" namespace.
logger = logging.getLogger("Unmanic.Plugin.keep_original_language_audio")


class Settings(PluginSettings):
    settings = {
        "sonarr_url":            "http://localhost:8989",
        "sonarr_api_key":        "",
        "radarr_url":            "http://localhost:7878",
        "radarr_api_key":        "",
        "additional_languages":  "",
        "keep_undefined":        True,
        "keep_commentary":       False,
    }

    def __init__(self, *args, **kwargs):
        super(Settings, self).__init__(*args, **kwargs)
        self.form_settings = {
            "sonarr_url": {
                "label": "Sonarr URL (e.g. http://localhost:8989)",
            },
            "sonarr_api_key": {
                "label":       "Sonarr API Key",
                "input_type":  "textarea",
            },
            "radarr_url": {
                "label": "Radarr URL (e.g. http://localhost:7878)",
            },
            "radarr_api_key": {
                "label":       "Radarr API Key",
                "input_type":  "textarea",
            },
            "additional_languages": {
                "label": "Additional languages to always keep "
                         "(comma-separated, e.g. 'eng, spa' or 'English, Spanish')",
            },
            "keep_undefined": {
                "label": "Keep audio tracks with no language tag (undefined)",
            },
            "keep_commentary": {
                "label": "Keep commentary tracks",
            },
        }


# ---------------------------------------------------------------------------
# ffprobe helpers
# ---------------------------------------------------------------------------

def _ffprobe_streams(file_path):
    """
    Return the list of stream dicts from ffprobe for the given file.
    Returns [] on any failure (caller treats that as "cannot analyse").
    """
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", file_path,
        ]
        raw = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return json.loads(raw).get("streams", [])
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        logger.warning("ffprobe failed for '%s': %s", file_path, exc)
        return []


def _audio_streams(streams):
    """Yield (audio_index, stream_dict) for each audio stream.

    audio_index is the 0-based position *within the audio streams only*, which is
    what ffmpeg's `-map 0:a:<n>` output-stream-specifier expects.
    """
    audio_index = 0
    for stream in streams:
        if stream.get("codec_type") == "audio":
            yield audio_index, stream
            audio_index += 1


def _is_commentary(stream):
    title = (stream.get("tags", {}) or {}).get("title", "") or ""
    return "commentary" in title.lower()


# ---------------------------------------------------------------------------
# Sonarr / Radarr lookups
# ---------------------------------------------------------------------------

def _arr_get(base_url, path, api_key, params=None):
    if requests is None:
        logger.error("The 'requests' library is not available; cannot query arr APIs.")
        return None
    if not base_url or not api_key:
        return None
    url = "{}/{}".format(base_url.rstrip("/"), path.lstrip("/"))
    headers = {"X-Api-Key": api_key}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - network/JSON errors are all non-fatal here
        logger.warning("arr request to '%s' failed: %s", url, exc)
        return None


def _path_matches(candidate_path, target_path):
    """
    True if candidate_path refers to the same file as target_path. Falls back to
    basename comparison so container path differences (Unmanic vs arr mounts)
    don't defeat the match.
    """
    if not candidate_path:
        return False
    if candidate_path == target_path:
        return True
    return os.path.basename(candidate_path) == os.path.basename(target_path)


def _radarr_original_language(file_path, settings):
    """Return the original-language ISO 639-2/B code for a movie file, or None."""
    url = settings.get_setting("radarr_url")
    key = settings.get_setting("radarr_api_key")
    movies = _arr_get(url, "/api/v3/movie", key)
    if not movies:
        return None
    for movie in movies:
        movie_file = movie.get("movieFile") or {}
        if _path_matches(movie_file.get("path"), file_path) or \
                _path_matches(movie.get("path"), os.path.dirname(file_path)):
            name = (movie.get("originalLanguage") or {}).get("name")
            code = to_iso639_2(name)
            if code:
                logger.info("Radarr: original language for '%s' is %s (%s)",
                            os.path.basename(file_path), name, code)
            return code
    return None


def _sonarr_original_language(file_path, settings):
    """Return the original-language ISO 639-2/B code for a TV file, or None.

    Uses Sonarr's parse endpoint to resolve the file to a series, then reads
    series.originalLanguage (available in Sonarr v4+).
    """
    url = settings.get_setting("sonarr_url")
    key = settings.get_setting("sonarr_api_key")
    if not url or not key:
        return None
    # Try the parse endpoint first - most reliable path->series resolution.
    parsed = _arr_get(url, "/api/v3/parse", key, params={"path": file_path})
    series = None
    if parsed:
        series = parsed.get("series")
    if not series:
        # Fall back: scan all series and match by the file's parent path.
        all_series = _arr_get(url, "/api/v3/series", key) or []
        for candidate in all_series:
            series_path = candidate.get("path")
            if series_path and os.path.normpath(series_path) in os.path.normpath(file_path):
                series = candidate
                break
    if not series:
        return None
    name = (series.get("originalLanguage") or {}).get("name")
    code = to_iso639_2(name)
    if code:
        logger.info("Sonarr: original language for '%s' is %s (%s)",
                    os.path.basename(file_path), name, code)
    return code


def _resolve_original_language(file_path, settings):
    """
    Resolve the original-language code, trying Sonarr (TV) then Radarr (Movies).
    Returns (code, source) where source is 'sonarr'/'radarr'/None.
    """
    code = _sonarr_original_language(file_path, settings)
    if code:
        return code, "sonarr"
    code = _radarr_original_language(file_path, settings)
    if code:
        return code, "radarr"
    return None, None


# ---------------------------------------------------------------------------
# Core decision logic (shared by both runners)
# ---------------------------------------------------------------------------

def _build_keep_set(file_path, settings):
    """
    Return (keep_codes, original_code) where keep_codes is the set of ISO 639-2/B
    codes to preserve. original_code may be None if unresolved.
    """
    original_code, source = _resolve_original_language(file_path, settings)
    keep_codes = set(normalise_keep_list(settings.get_setting("additional_languages")))
    if original_code:
        keep_codes.add(original_code)
    return keep_codes, original_code


def _plan_audio(file_path, settings):
    """
    Analyse the file and decide which audio streams to keep.

    Returns a dict:
        {
          "analysed":     bool,   # ffprobe produced audio streams
          "resolvable":   bool,   # we have a non-empty keep-set
          "total_audio":  int,
          "keep_audio_idx": [int, ...],   # audio-relative indices to keep
          "remove_count": int,
          "reason":       str,
        }
    """
    streams = _ffprobe_streams(file_path)
    audio = list(_audio_streams(streams))
    result = {
        "analysed": bool(streams),
        "resolvable": False,
        "total_audio": len(audio),
        "keep_audio_idx": [a_idx for a_idx, _ in audio],
        "remove_count": 0,
        "reason": "",
    }

    if not audio:
        result["reason"] = "No audio streams found; nothing to do."
        return result

    keep_codes, original_code = _build_keep_set(file_path, settings)
    keep_undefined = bool(settings.get_setting("keep_undefined"))
    keep_commentary = bool(settings.get_setting("keep_commentary"))

    if not keep_codes:
        # Fail safe: we have no basis to decide. Keep everything.
        result["reason"] = ("Original language unresolved and no additional "
                            "languages configured; keeping all audio.")
        return result

    result["resolvable"] = True

    keep_idx = []
    for a_idx, stream in audio:
        lang = stream_language(stream.get("tags", {}))
        keep = False
        if lang == "und":
            keep = keep_undefined
        elif lang in keep_codes:
            keep = True
        # Commentary handling: only drop an otherwise-kept track when the user
        # has opted out of commentary and the track isn't in a wanted language.
        if keep and _is_commentary(stream) and not keep_commentary and lang not in keep_codes:
            keep = False
        if keep:
            keep_idx.append(a_idx)

    # Fail safe: never strip all audio.
    if not keep_idx:
        result["reason"] = ("Rule would remove every audio track; keeping all "
                            "audio instead (fail-safe).")
        result["keep_audio_idx"] = [a_idx for a_idx, _ in audio]
        return result

    result["keep_audio_idx"] = keep_idx
    result["remove_count"] = len(audio) - len(keep_idx)
    if original_code:
        result["reason"] = ("Keeping {} of {} audio tracks (original language "
                            "'{}').".format(len(keep_idx), len(audio), original_code))
    else:
        result["reason"] = ("Keeping {} of {} audio tracks (keep-list only)."
                            .format(len(keep_idx), len(audio)))
    return result


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def on_library_management_file_test(data):
    """
    Runner: decide whether a file should be added to the pending task queue.

    'data' includes:
        path                      - String, the file path to test.
        issues                    - List, reasons the file should/shouldn't process.
        add_file_to_pending_tasks - Boolean, whether to queue the file.
    """
    settings = Settings(library_id=data.get("library_id"))
    file_path = data.get("path")

    plan = _plan_audio(file_path, settings)

    if plan["remove_count"] > 0:
        data["add_file_to_pending_tasks"] = True
        data["issues"].append({
            "id": "keep_original_language_audio",
            "message": "Audio cleanup required: {}".format(plan["reason"]),
        })
        logger.info("Queued '%s': %s", file_path, plan["reason"])
    else:
        # Leave add_file_to_pending_tasks as-is (don't override other plugins).
        logger.info("No action for '%s': %s", file_path, plan["reason"])

    return data


def on_worker_process(data):
    """
    Runner: build the ffmpeg command that keeps only the wanted audio streams.

    'data' includes:
        exec_command       - Array, the subprocess command Unmanic executes.
        file_in            - String, source file to process.
        file_out           - String, destination for the processed file.
        original_file_path - String, absolute path to the original file.
        repeat             - Boolean, run this runner again when done.
    """
    data["exec_command"] = []
    data["repeat"] = False

    settings = Settings(library_id=data.get("library_id"))
    # Decide based on the ORIGINAL file (arr lookups match the real library path),
    # but analyse streams on file_in (the current cache file).
    lookup_path = data.get("original_file_path") or data.get("file_in")
    file_in = data.get("file_in")

    # Resolve the keep-set from the original path, but enumerate streams on file_in.
    keep_codes, original_code = _build_keep_set(lookup_path, settings)
    keep_undefined = bool(settings.get_setting("keep_undefined"))
    keep_commentary = bool(settings.get_setting("keep_commentary"))

    streams = _ffprobe_streams(file_in)
    audio = list(_audio_streams(streams))

    if not audio:
        logger.info("No audio streams in '%s'; skipping.", file_in)
        return data

    if not keep_codes:
        logger.info("No keep-set for '%s'; leaving audio untouched.", file_in)
        return data

    keep_idx = []
    for a_idx, stream in audio:
        lang = stream_language(stream.get("tags", {}))
        keep = False
        if lang == "und":
            keep = keep_undefined
        elif lang in keep_codes:
            keep = True
        if keep and _is_commentary(stream) and not keep_commentary and lang not in keep_codes:
            keep = False
        if keep:
            keep_idx.append(a_idx)

    # Fail safe: never strip all audio, and skip work if nothing is removed.
    if not keep_idx:
        logger.warning("Rule would strip all audio from '%s'; skipping (fail-safe).", file_in)
        return data
    if len(keep_idx) == len(audio):
        logger.info("All audio tracks already wanted in '%s'; skipping.", file_in)
        return data

    file_out = data.get("file_out")
    # Preserve the original container extension.
    in_ext = os.path.splitext(file_in)[1]
    if file_out:
        base = os.path.splitext(file_out)[0]
        file_out = base + in_ext
        data["file_out"] = file_out

    # Build the ffmpeg map: all video + subtitles + only wanted audio streams.
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", file_in,
           "-map", "0:v?", "-map", "0:s?", "-map", "0:t?"]
    for a_idx in keep_idx:
        cmd += ["-map", "0:a:{}".format(a_idx)]
    cmd += ["-c", "copy", "-y", file_out]

    data["exec_command"] = cmd
    logger.info("Stripping %d audio track(s) from '%s' (keeping audio %s, original '%s')",
                len(audio) - len(keep_idx), file_in, keep_idx, original_code)
    return data
