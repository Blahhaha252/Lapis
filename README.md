# Lapis

Lapis is a Python-based media processing pipeline for preparing video content for
storage and HLS playback. It uses FFmpeg for media processing and MediaInfo as its
primary source of input stream metadata.

Lapis supports multilingual media without a fixed language allowlist. Audio and
subtitle tracks are detected from the source file, preserved where supported, and
published as selectable HLS tracks.

## Features

- Multiple audio tracks per source file
- HLS alternate-audio groups
- Multiple tracks in the same language
- Commentary and audio-description tracks
- Unicode WebVTT subtitles
- Regional and script language tags such as `pt-BR`, `zh-Hant`, and `zh-Hant-TW`
- Unlabelled and undetermined-language tracks
- Track titles from source metadata
- Per-track language and title overrides
- Automatic HLS master playlist generation
- Multiple video qualities with shared audio encodes
- Video-only input support
- Movie, show, anime, and OVA content organization
- Track metadata exported to `hls/tracks.json`

There is no fixed list of supported spoken languages. If a language can be
represented by the source metadata and processed by the underlying media tools,
Lapis can retain it.

## Requirements

Lapis requires Python and the following system tools:

- FFmpeg
- FFprobe
- MediaInfo

On Ubuntu:

```sh
sudo apt install ffmpeg mediainfo
```

Install the Python dependencies inside a virtual environment:

```sh
python -m pip install -r requirements.txt
```

Lapis uses `mi/mediainfo` when a bundled MediaInfo executable is present.
Otherwise, it uses `mediainfo` from `PATH`.

The optional `langcodes[data]` dependency provides language-name lookup and
standard language-tag normalization. Tracks continue to work without the language
database, but their labels may use language codes instead of human-readable
language names.

## Media metadata

MediaInfo is the authoritative metadata source for input video, audio, and
subtitle streams.

Lapis uses MediaInfo metadata including:

- Language
- Track title
- Channel count
- Sample rate
- Bitrate
- Track flags

FFprobe is not used to determine input language tags or dispositions. It remains
in use for duration and segment measurements and for inspecting generated H.264
codec parameters.

FFmpeg performs the actual media encoding and conversion.

Input tracks are associated with FFmpeg using their per-type ordinal:

```text
0:a:0
0:a:1
0:s:0
0:s:1
```

MediaInfo `ID` and `StreamOrder` values are retained for diagnostic purposes but
are not assumed to correspond to FFmpeg stream indices.

## Audio tracks

All input audio tracks are retained as selectable HLS alternate audio.

This includes sources containing:

- Multiple languages
- Multiple tracks in one language
- Commentary tracks
- Audio-description tracks
- Different mixes or dubbing releases

Missing or unusable language metadata is represented as:

```text
und
```

for an undetermined language.

`zxx`, representing no linguistic content, is also retained.

Language tags are not restricted to a hardcoded list. Regional, script, rare, and
private-use tags can be retained.

Examples include:

```text
en
ja
ru
pt-BR
zh-Hant
zh-Hant-TW
```

Unknown tags fall back to their language-code representation when a friendly
language name cannot be resolved.

Each HLS video quality references an alternate-audio group. Identical audio
encodes can be shared between video qualities rather than encoded separately for
each quality.

More source audio tracks therefore require additional encoding time and storage,
but not necessarily one duplicate encode per video quality.

Video-only sources are also supported and do not advertise nonexistent audio
tracks.

## Subtitles

Supported text subtitle streams are converted to Unicode WebVTT and published as
HLS subtitle tracks.

Subtitle tracks do not require language metadata. Unlabelled tracks are retained,
and duplicate display names are disambiguated.

Bitmap subtitle formats such as PGS and DVD subtitles cannot be directly
converted to WebVTT.

By default, an unsupported subtitle conversion produces an explanatory error
rather than silently discarding the stream.

Unsupported subtitles can explicitly be omitted from HLS output with:

```sh
--skip-unsupported-subtitles
```

The copied original media file still retains those streams.

Lapis targets standalone subtitle streams. Embedded closed-caption services may
need to be extracted into separate subtitle tracks before processing.

WebVTT conversion cannot preserve every advanced ASS/SSA feature, including some
typography, positioning, and karaoke effects.

## Track metadata

Detected track information and generated output locations are written to:

```text
hls/tracks.json
```

This provides a machine-readable description of the input tracks and the HLS
assets generated from them.

## Content language versions

For shows and anime, `--language` identifies the language/version of the source
video and its directory layout.

It does **not** define the language of every audio or subtitle stream inside the
file.

For example:

```sh
python main.py \
  --input episode.mkv \
  --content-type anime \
  --imdb tt1234567 \
  --season 1 \
  --episode 1 \
  --rating TV-PG \
  --language japanese
```

produces the existing layout:

```text
<output>/tt1234567/s1/japanese/e1.mkv
<output>/tt1234567/s1/hls/japanese/e1/master.m3u8
```

`--language` accepts a safe ASCII name or language-code slug and has no language
allowlist.

For native-script display names, use a language code for the directory and retain
the native name in the media track title.

Separate source video versions remain separate entries. For example, the same
episode can be processed once using:

```sh
--language japanese
```

and again using:

```sh
--language english
```

Both versions remain represented in the episode metadata.

Movie and OVA language variants remain outside this directory convention, but
multilingual audio and subtitle tracks are supported for those content types.

## Correcting track metadata

Incorrect or missing embedded language metadata can be overridden from the
command line.

Track indices are zero-based within their own stream type. Audio and subtitle
indices are therefore independent.

Example:

```sh
python main.py \
  --input episode.mkv \
  --content-type anime \
  --imdb tt1234567 \
  --season 1 \
  --episode 1 \
  --rating TV-PG \
  --language japanese \
  --audio-language 0=ja \
  --audio-language 1=yue \
  --audio-language 2=pt-BR \
  --audio-title '1=粵語' \
  --default-audio 0 \
  --subtitle-language 0=zh-Hant \
  --subtitle-title '0=繁體中文'
```

The relevant options can be repeated for additional tracks.

Use language tags with the language options and free-form or native-script names
with the title options.

Without `--default-audio`, Lapis selects the first source audio track marked as
default. If no audio track is marked default, the first audio track is used.

Only one audio track is advertised as the default for an HLS audio group. All
other tracks remain manually selectable.

## HLS player compatibility

Applications should load:

```text
master.m3u8
```

rather than an individual quality playlist.

Quality playlists contain video, while the HLS master playlist associates them
with their alternate audio and subtitle groups.

The player must therefore support HLS alternate audio.

For hls.js-based applications, audio tracks are available through the
`audioTracks` list and can be selected through `audioTrack`.

The language/version selector used by `watch.btube3.com` is separate from the
audio-track selector. A video can therefore have both:

```text
Japanese video version
├── Japanese audio
├── English audio
└── Commentary

English video version
├── English audio
└── Commentary
```

Player applications must provide their own audio-selection UI if alternate audio
tracks should be exposed to users.

Lapis generates the media and playlists; it does not modify player-side
JavaScript.

## Timing

Separate audio and video encodes preserve the source timeline.

Lapis uses the same one-second MPEG-TS timestamp offset for generated streams,
with a corresponding WebVTT timestamp map.

Audio encoder priming is retained instead of independently resetting each
track's presentation timestamps.

Existing source audio/video synchronization offsets are preserved and are not
automatically corrected.

## Publishing

The HLS master playlist is replaced after successful stream encoding.

Media assets are written into their configured output directories. When replacing
content that is currently being served to users, rendering into an offline or
staging output tree is recommended.

Changes to the audio layout require existing content to be rendered again.
Replacing the Lapis scripts alone does not alter previously generated output.

## Limitations

Lapis preserves and processes language metadata already associated with the
source media.

It does **not**:

- Translate audio or subtitles
- Transcribe speech
- Detect the spoken language of unlabelled audio
- Generate missing language tracks
- Preserve every advanced ASS/SSA styling feature
- Convert bitmap subtitles such as PGS/DVD subtitles directly to WebVTT

Input codecs must also be supported by the installed FFmpeg build.

## Testing

Install the test dependencies:

```sh
python -m pip install pytest m3u8 pymediainfo
```

Run the test suite:

```sh
python -m pytest -q tests
```

The test suite covers:

- Real FFmpeg output
- Multiple audio tracks
- Multiple subtitle tracks
- Unicode subtitle text
- Regional and script language tags
- Unlabelled tracks
- Multiple video qualities
- Delayed audio tracks
- Playlist bitrate measurements
- Generated H.264 codec declarations
- Existing episode JSON paths
- Multiple video-language versions of one episode

MediaInfo metadata can be exercised through the real MediaInfo shared library
using `pymediainfo` when its CLI is unavailable during testing. Production uses
the MediaInfo CLI.

FFmpeg encoding and FFprobe output checks are exercised directly.

Browser playback behavior is outside the automated test suite.

## License

See the repository license for usage and redistribution terms.