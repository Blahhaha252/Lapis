# Lapis

Lapis prepares downloadable media and HLS streams from video, audio and
subtitles. Use explicit inputs (`-v`, `-a`, `-s`) to choose tracks yourself, or
`--input` to discover the tracks inside one existing container.

FFmpeg handles media processing. **MediaInfo remains the source of input stream
metadata**, including languages, dimensions, frame rates and channel counts.
FFprobe is used for duration/segment measurements and generated codec parameters.

## Installation

Python 3.10 or later is required. On Ubuntu:

```sh
sudo apt install ffmpeg mediainfo python3-venv
python3 -m venv venv
. venv/bin/activate
python -m pip install -r requirements.txt
```

Run `python main.py ...` or `./lapis ...` in that environment.
`--mediainfo-path /path/to/mediainfo` selects an executable explicitly. Otherwise
Lapis uses `mi/mediainfo` if present, then `mediainfo` on PATH. An explicit path
is respected rather than silently falling back if it fails.

The default output directory comes from `./config.yml`:

```yaml
output: /path/to/cdn
```

Use `--config /path/to/config.yml` for another configuration, or
`--output /path/to/cdn` to override it without loading a configuration file.

## One video with several audio and subtitle inputs

```sh
python main.py \
  -v video.mkv \
  -a audio.m4a -al english \
  -a audio.aac -al spanish \
  -s subtitle.vtt -sl english \
  -s subtitle2.vtt -sl spanish \
  --content-type movie --imdb tt1234567 --rating PG \
  --movie-name 'Example movie' --output ./cdn
```

With one video and no joins, all declared audio and subtitle inputs are included
in one downloadable MKV and one HLS master with selectable tracks.

## Separate HLS playlists

HLS is always packaged as separate video, audio and subtitle playlists. Each
playlist is encoded directly from its selected source file and stream; HLS
does not read back the combined MKV. Add `--hls-only` to skip creating that
download altogether:

```sh
python main.py \
  -v video.mkv \
  -a english.m4a -al english \
  -a spanish.aac -al spanish \
  -s english.vtt -sl english \
  -s spanish.vtt -sl spanish \
  --content-type movie --imdb tt1234567 --rating PG \
  --language english --output ./cdn --hls-only
```

The player opens `tt1234567/hls/english/master.m3u8`. Paths underneath that
directory are:

| Output | Contents |
| --- | --- |
| `master.m3u8` | Links the video qualities and selectable audio/subtitle groups |
| `video/<quality>/<quality>-playlist.m3u8` | Video-only H.264 segments |
| `audio/track-<index>-<channels>ch-<bitrate>/audio-playlist.m3u8` | One audio track's AAC segments |
| `vtt/subtitle-<index>-playlist.m3u8` | One subtitle track's WebVTT file |
| `tracks.json` | Track metadata and relative playlist paths, including video renditions |

Indices in generated names are padded to three digits (`000`, `001`, etc.).
Video and audio use approximately ten-second MPEG-TS segments. Each subtitle
playlist references one WebVTT file covering the encoded video's duration,
including intervals without cues. These are VOD playlists ending in
`#EXT-X-ENDLIST`.

The master uses `EXT-X-STREAM-INF` for each video quality and `EXT-X-MEDIA`
for audio and subtitles, connected through `AUDIO` and `SUBTITLES` group IDs.
No audio is embedded in the video segments. Compatible players can change
audio language or subtitles while keeping the same video quality.

The same packaging applies to every `-join` and to legacy `--input` mode.
Each join has its own master: different video edits stay separate so a player
cannot automatically switch to another edit as if it were a quality change.
To offer several audio languages with one video, include them in the same join,
for example `-join 0:v 0:a 1:a -jl english`.

Without `--hls-only`, explicit mode additionally makes the downloadable MKV;
legacy mode additionally copies its original container. In HLS-only mode,
content JSON writes `"download": null`. The player should hide its download
button for this value. An older download file is not deleted, but its stale
link is cleared when metadata for that version/episode is updated.

Upgrade note: video playlists now live under `video/` inside each HLS tree.
Master URLs are unchanged. Follow their relative playlist links rather than
hardcoding the old quality-directory paths; old unused files are not removed.

## Input labels and languages

Each input selects stream **0 of its requested type** by default. Explicit
`-v video.mkv` selects video only; it does not also import embedded audio or
subtitles. Supply the same file with `-a` or `-s` to select those streams. Without
any `-a` inputs, the output is video-only.

`-al` labels the most recent `-a`; `-sl` labels the most recent `-s`. Names such as
`english`, `spanish`, `japanese` and `日本語` resolve through `langcodes[data]`.
Language tags such as `en`, `ja`, `yue`, `pt-BR`, `zh-Hant` and `x-custom` have no
fixed allowlist. For ambiguous names, use a tag. Omit the label to retain
MediaInfo's language metadata, or `und` when missing. Labels do not translate
speech or text, and filenames are never used to guess the spoken language.

AAC normally has the `.aac` extension. Files named `.acc` are also accepted if
FFmpeg recognizes their actual contents; no extension allowlist is imposed.

## Couple inputs into separate versions

Indices start at zero independently for each input type:

| Reference | Selected input |
| --- | --- |
| `0:v` | First `-v` |
| `1:v` | Second `-v` |
| `0:a` | First `-a` |
| `1:a` | Second `-a` |
| `0:s` | First `-s` |

These are Lapis references, not FFmpeg's combined input indices.

```sh
python main.py \
  -v english-video.mkv -v japanese-video.mkv \
  -a english.m4a -al english \
  -a japanese.aac -al japanese \
  -s english.vtt -sl english \
  -s japanese.vtt -sl japanese \
  -join 0:v 0:a 0:s -jl english \
  --join 1:v 1:a 1:s -jl japanese \
  --content-type anime --imdb tt1234567 --rating TV-PG \
  --season 1 --episode 1 --show-name 'Example show' \
  --episode-name 'First episode' --output ./cdn
```

`-join` and `--join` are aliases. Each join couples exactly one video with zero
or more audio inputs. For alternate audio within one version, use e.g.
`-join 0:v 0:a 1:a -jl japanese`. Inputs may be reused across joins.
Joins combine tracks on one timeline; they do not concatenate videos.

`-jl`, `--join-language` and `--join-lang` label the preceding join's output
folder/version. Multiple videos require explicit joins. Multiple joins require
distinct labels to prevent output collisions.

**Subtitles are shared unless a join explicitly selects a subset.** For example,
`-join 0:v 0:a` includes all declared `-s` inputs, while
`-join 0:v 0:a 1:s` includes only the second subtitle. Audio is never implicitly
added to an explicit join. Unreferenced audio/video declarations are not used.

Input paths and references are validated before processing. All selected stream
indices and subtitle support are checked before the first join is written.
Missing files, nonexistent streams, malformed/duplicate references and duplicate
version folders produce errors instead of silently choosing another track.

## Preview the selection

Add `--dry-run` to either mode:

```sh
python main.py -v video.mkv -a english.m4a -al english \
  -join 0:v 0:a -jl english \
  --content-type movie --imdb tt1234567 --rating PG \
  --output ./cdn --dry-run > plan.json
```

The JSON includes selected inputs, languages, stream indices, output paths,
direct HLS source options and mux commands. With `--hls-only`, the download
path (`paths.original`) and `mux_command` are both null. MediaInfo probes the
selected sources, but no output directories or media are created. Warnings use
stderr so stdout stays valid JSON. A dry run
cannot guarantee encoder/muxer support for every codec; FFmpeg checks that when
processing runs.

## Pick a stream inside a container

```sh
python main.py \
  -v episode.mkv --video-stream 0 \
  -a episode.mkv --audio-stream 1 -al japanese \
  -a episode.mkv --audio-stream 2 -al english \
  -s episode.mkv --subtitle-stream 0 -sl english \
  --content-type show --imdb tt1234567 --rating TV-PG \
  --season 1 --episode 2 --language japanese
```

`--video-stream`, `--audio-stream` and `--subtitle-stream` modify the preceding
input of the corresponding type. Indices are zero-based within that stream type
inside the file. In this example, join reference `0:a` still means the first
`-a`, even though it selects the container's second audio stream.

## Timing

Inputs must correspond to the same edit. Lapis does not detect dub alignment,
remove different openings, stretch audio, or synchronize recordings by content.
Each distinct input is shifted from its container start time. Relative stream
offsets within a shared container are retained. Remuxing can change timestamp
precision and how codec priming is represented.

To adjust a track, use an offset after its input:

```sh
-v video.mkv \
-a audio.m4a -al english --audio-offset 0.250 \
-s subtitle.vtt -sl english --subtitle-offset -0.100
```

Positive values delay the preceding input; negative values advance it. Material
moved before the start may not be playable. Inspect the result after adjusting
timing. Lapis does not use `-shortest` to silently truncate a video to an audio
track's length.

Each HLS stream is encoded directly from its input using the same container
start-time rule and explicit track offset. Video/audio share a one-second
MPEG-TS timestamp offset; WebVTT uses the matching timestamp map. Constant-rate
video may round timestamps to its frame interval, and AAC encoder priming adds
a short initial packet. This does not establish content alignment between
unrelated source files. The optional downloadable MKV is a separate stream-copy
output, not an intermediate used to build the HLS playlists.

## Language folders and metadata

`--language english` sets the version folder for a single output. Each `-jl`
overrides it for its join. Directory names are safe ASCII names/codes; track
labels such as `-al 日本語` are separate. Use `ja` or `japanese` for that folder.

By default, explicit mode produces an MKV containing the selected streams, without a
`raw_files` directory:

| Content | Download | HLS master |
| --- | --- | --- |
| Movie/OVA | `<imdb>/<language>/<imdb>.mkv` | `<imdb>/hls/<language>/master.m3u8` |
| Show/anime with season | `<imdb>/s1/<language>/e1.mkv` | `<imdb>/s1/hls/<language>/e1/master.m3u8` |
| Show/anime without season | `<imdb>/<language>/e1.mkv` | `<imdb>/hls/<language>/e1/master.m3u8` |

Omitting a language keeps the unlabelled layout. Legacy `--input` copies the
original container with its original extension.

One `<content-type>.json` describes all versions. Episodes are stored under
`seasons[season].languages[language].episodes`, or top-level
`languages[language].episodes` without seasons. Episode entries contain `name`,
`master`, and `download`. Movie/OVA versions live in top-level `languages` and
contain `master` and `download` directly. Paths are relative to the CDN root.
Other episodes, seasons and versions are retained. JSON updates are locked and
atomically replaced.

An existing single rating-suffixed movie/OVA index, such as `movie-kids.json`, is
read on upgrade when `movie.json` is absent. Multiple legacy indices must be
merged explicitly; old files are not deleted. Update any `global.json` entries
that reference a rating-suffixed filename to the canonical filename. Verify that
your player supports object-valued episodes and movie language variants before
publishing the new index.

Each HLS directory contains `tracks.json` with selected track metadata,
`video_renditions`, `audio_groups`, and `published_subtitles`. Every published
playlist path is relative to that HLS directory. Source filenames and indices are recorded without
exposing absolute local source directories.

## Existing scripts and overrides

Legacy automatic discovery remains available:

```sh
python main.py --input episode.mkv \
  --content-type anime --imdb tt1234567 --rating TV-PG \
  --season 1 --episode 1 --language japanese
```

Do not mix `--input` with `-v`/`-a`/`-s`. An explicit input is now required; there
is no implicit `./test.mkv` fallback.

Older overrides remain supported:

```sh
--audio-language 0=en --audio-title '0=English dub' \
--audio-language 1=ja --default-audio 1 \
--subtitle-language 0=en --subtitle-title '0=English SDH'
```

These indices refer to output track order and apply to **each** join. Prefer
`-al`/`-sl` when joins select different tracks. Output overrides take precedence
over input labels. `--default-audio` chooses one selected audio track; otherwise
the first default-marked track is used, then the first selected track.

Quality, frame-rate, CRF, cover, nice, title, rating and year options remain
available with `--help`. Global technical video overrides apply to every video.

## Playback and limitations

HLS qualities are video-only; the master associates them with AAC audio groups
and WebVTT subtitles. Players need alternate-audio support and selection UI.
Audio encodes with identical settings are reused across qualities of one
version. Different joins are independent encoding jobs.

Text subtitles are converted to WebVTT; advanced ASS/SSA styling and font
attachments are not preserved. Bitmap PGS/DVD subtitles require OCR or a
specialist converter. They fail preflight by default;
`--skip-unsupported-subtitles` omits them from HLS and retains the selected
stream in the download if a download is being created. Embedded captions may
need separate extraction.

Input codecs must be supported by FFmpeg. Without `--hls-only`, explicit-mode
inputs must also be supported by Matroska for the stream-copy download.
Unselected attachments/data streams are not imported. No language
translation, transcription or speech-language recognition is performed.

Downloads are replaced only after muxing succeeds. The temporary MKV is written
in its destination directory to avoid a second full media copy. Ordinary
failures and Ctrl+C clean it up; a hard kill/power loss may leave a hidden
`.lapis-join-*.mkv` for manual cleanup. Selected input files cannot be overwritten
by an explicit-mode output and are never deleted.

The master playlist follows successful encoding, and content JSON follows media
and cover work. The entire HLS directory is not transactional: use a staging
`--output` when replacing live content. If a later join fails, earlier successful
joins remain published. Several versions require additional time and storage.

## Tests

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
```

Tests cover argument binding, independent indices, invalid joins, language names
and tags, content layouts, metadata preservation, mux failure cleanup, real
MKV/HLS output, stream selection, audio/subtitles, copied video packet hashes,
positive audio offsets, default audio, multiple qualities, fractional frame
rates, visible dimensions and legacy discovery. Separate-HLS checks cover
HLS-only output, stream purity, actual selected video/audio content, subtitle
offsets, nonzero container timestamps, segment boundaries, isolated joins,
dry runs, metadata paths and retaining old download files.

Integration tests use FFmpeg and MediaInfo. If the MediaInfo command is absent,
the adapter invokes the actual MediaInfo shared library from `pymediainfo`.
Browser playback and full-length production encodes are outside this suite.
