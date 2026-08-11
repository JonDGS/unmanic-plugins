#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Language normalisation helpers for the Keep Original Language Audio plugin.

Media stream language tags (from ffprobe) use ISO 639-2 codes (e.g. "spa",
"jpn", "eng"). Sonarr/Radarr report language *names* (e.g. "Spanish", "Japanese")
and users may type either names or 2-/3-letter codes into the settings.

`to_iso639_2` normalises any of those into a canonical ISO 639-2/B code so they
can be compared. The map covers the common languages handled by Sonarr/Radarr;
unknown input returns None (callers treat that as "unresolved").

No third-party dependency is required for this module.
"""

# Canonical ISO 639-2/B code -> set of accepted aliases (all lowercase).
# Includes the 639-2/B code itself, the 639-2/T variant where it differs,
# the 639-1 two-letter code, and the English language name.
_LANGUAGE_ALIASES = {
    "eng": {"eng", "en", "english"},
    "spa": {"spa", "es", "spanish", "castilian"},
    "fre": {"fre", "fra", "fr", "french"},
    "ger": {"ger", "deu", "de", "german"},
    "ita": {"ita", "it", "italian"},
    "por": {"por", "pt", "portuguese"},
    "dut": {"dut", "nld", "nl", "dutch", "flemish"},
    "rus": {"rus", "ru", "russian"},
    "jpn": {"jpn", "ja", "japanese"},
    "chi": {"chi", "zho", "zh", "chinese", "mandarin", "cantonese"},
    "kor": {"kor", "ko", "korean"},
    "ara": {"ara", "ar", "arabic"},
    "hin": {"hin", "hi", "hindi"},
    "ben": {"ben", "bn", "bengali"},
    "tel": {"tel", "te", "telugu"},
    "tam": {"tam", "ta", "tamil"},
    "mal": {"mal", "ml", "malayalam"},
    "kan": {"kan", "kn", "kannada"},
    "mar": {"mar", "mr", "marathi"},
    "pan": {"pan", "pa", "punjabi"},
    "urd": {"urd", "ur", "urdu"},
    "tha": {"tha", "th", "thai"},
    "vie": {"vie", "vi", "vietnamese"},
    "ind": {"ind", "id", "indonesian"},
    "may": {"may", "msa", "ms", "malay"},
    "tur": {"tur", "tr", "turkish"},
    "pol": {"pol", "pl", "polish"},
    "ukr": {"ukr", "uk", "ukrainian"},
    "cze": {"cze", "ces", "cs", "czech"},
    "slo": {"slo", "slk", "sk", "slovak"},
    "hun": {"hun", "hu", "hungarian"},
    "rum": {"rum", "ron", "ro", "romanian"},
    "gre": {"gre", "ell", "el", "greek"},
    "bul": {"bul", "bg", "bulgarian"},
    "ser": {"ser", "srp", "sr", "serbian"},
    "hrv": {"hrv", "hr", "croatian"},
    "slv": {"slv", "sl", "slovenian"},
    "swe": {"swe", "sv", "swedish"},
    "nor": {"nor", "no", "norwegian"},
    "dan": {"dan", "da", "danish"},
    "fin": {"fin", "fi", "finnish"},
    "ice": {"ice", "isl", "is", "icelandic"},
    "heb": {"heb", "he", "hebrew"},
    "per": {"per", "fas", "fa", "persian", "farsi"},
    "fil": {"fil", "tgl", "tl", "filipino", "tagalog"},
    "cat": {"cat", "ca", "catalan"},
    "baq": {"baq", "eus", "eu", "basque"},
    "glg": {"glg", "gl", "galician"},
    "afr": {"afr", "af", "afrikaans"},
    "swa": {"swa", "sw", "swahili"},
    "est": {"est", "et", "estonian"},
    "lav": {"lav", "lv", "latvian"},
    "lit": {"lit", "lt", "lithuanian"},
    "lat": {"lat", "la", "latin"},
}

# Reverse lookup: alias -> canonical 639-2/B code. Built once at import.
_ALIAS_TO_CODE = {}
for _code, _aliases in _LANGUAGE_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CODE[_alias] = _code

# Values that ffprobe / arr may use to mean "no language".
_UNDEFINED_VALUES = {"", "und", "unknown", "undetermined", "mis", "mul", "zxx", "no linguistic content"}


def to_iso639_2(value):
    """
    Normalise a language name or code to its ISO 639-2/B code.

    Accepts an English name ("Japanese"), a 639-1 code ("ja"), or a 639-2 code
    ("jpn" / "jap"). Case- and whitespace-insensitive.

    Returns the canonical 639-2/B code (str) or None if the value is unknown or
    explicitly undefined.
    """
    if not value:
        return None
    key = str(value).strip().lower()
    if key in _UNDEFINED_VALUES:
        return None
    return _ALIAS_TO_CODE.get(key)


def stream_language(stream_tags):
    """
    Extract a normalised language code from an ffprobe stream's "tags" dict.

    Returns the canonical 639-2/B code, or "und" when the stream has no usable
    language tag (so callers can apply the keep_undefined rule consistently).
    """
    if not stream_tags:
        return "und"
    raw = stream_tags.get("language") or stream_tags.get("LANGUAGE") or ""
    code = to_iso639_2(raw)
    return code if code else "und"


def normalise_keep_list(raw_csv):
    """
    Parse the user's "additional languages" comma-separated setting into a set of
    canonical 639-2/B codes. Unrecognised entries are silently dropped.
    """
    result = set()
    if not raw_csv:
        return result
    for part in str(raw_csv).replace(";", ",").split(","):
        code = to_iso639_2(part)
        if code:
            result.add(code)
    return result
