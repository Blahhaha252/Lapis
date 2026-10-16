# Lapis language-variant migration — GPT-6

This branch keeps one **independently encoded media source per language
version**. Do not mix alternate audio streams into an HLS master.

## Input validation

- Exactly one video stream and one audio stream are required.
- Subtitle streams are still accepted and can be exported to WebVTT when supported.
- A second audio/video stream causes an error **before the source is copied or FFmpeg encodes it**.
- Multi-language editions are supplied as separate source files with separate
  `--language` calls, not as multiple audio tracks in one MKV.

## Language vocabulary

The project's existing `langcodes[data]` dependency supplies broad IANA/ISO
language-code and language-name information (including uncommon and regional
languages). Lapis does **not** contain an artificial 20- or 50-language
allowlist. Explicit directory language names remain lowercase, safe slugs
(e.g. `english`, `japanese`, `yue`, `pt-br`). It is not possible to
claim that any language database covers every spoken language ever used; the
slug mechanism intentionally permits additional names without a code update.

The `--language` argument specifies the language of **this source version**,
not an instruction to select audio by stream metadata or to translate media.

## New movie paths

```text
<output>/tt123/english/tt123.mkv
<output>/tt123/hls/english/master.m3u8
<output>/tt123/japanese/tt123.mp4
<output>/tt123/hls/japanese/master.m3u8
```

Without `--language`, original paths are retained:

```text
<output>/tt123/tt123.mkv
<output>/tt123/hls/master.m3u8
```

Shows/anime continue to use:

```text
<output>/tt456/s1/english/e1.mkv
<output>/tt456/s1/hls/english/e1/master.m3u8
```

Each movie's top-level `movie[-rating].json` has a `languages` map keyed by
language slug. Each entry stores CDN-root-relative `original` and `master`
paths. The legacy unlabelled movie stores those fields at the top level.
Episodes now store `name`, `original` and `master` in object form, while
the reader remains compatible with historical string episode names.

The watch site should be updated together with this branch to expose
the correct version selector and original-file download.

## Example commands

```bash
python main.py --input /media/english.mkv --imdb tt123 \
  --content-type movie --rating PG --language english --movie-name 'Example'
python main.py --input /media/japanese.mp4 --imdb tt123 \
  --content-type movie --rating PG --language japanese --movie-name 'Example'
```

**Deployment caution:** The updated encoder and index/player contract should be
tested against real FFmpeg/MediaInfo source media before replacing production.
