# Keep Original Language Audio

Removes audio tracks from a media file that are **not** in the item's original
language, keeping the file tidy and smaller while preserving the audio that
matters.

## How it works

1. When a file enters the library, the plugin looks up the item's **original
   language** from your automation stack:
   - **TV episodes** → Sonarr (`series.originalLanguage`, Sonarr v4+)
   - **Movies** → Radarr (`movie.originalLanguage`)
2. It builds a **keep-set** of languages: the original language, plus any extra
   languages you list in the settings, plus (optionally) untagged/undefined
   tracks and commentary tracks.
3. Any audio stream whose language is not in the keep-set is removed with a fast,
   lossless `ffmpeg -c copy` remux. Video and subtitle streams are always kept.

## Settings

- **Sonarr URL / API Key** — your Sonarr instance (default `http://localhost:8989`).
- **Radarr URL / API Key** — your Radarr instance (default `http://localhost:7878`).
- **Additional languages to keep** — comma-separated ISO 639-2 codes or names
  (e.g. `eng, spa` or `English, Spanish`) always preserved in addition to the
  original.
- **Keep undefined/untagged audio tracks** — when on (default), audio streams
  with no language tag are preserved.
- **Keep commentary tracks** — when off (default), tracks whose title contains
  "commentary" are removed unless their language is otherwise kept.

## Fail-safe behaviour

The plugin will **never** produce a file with no audio. If the original language
cannot be resolved (Sonarr/Radarr not configured or no match) **and** no
additional languages are set, the file is left untouched. If the computed rule
would remove *every* audio track, all audio is kept instead.
