# Lapis — unified single-stream publisher

Lapis requires **one and only one stream per input file**, and an explicit language
on **every** input. MediaInfo-derived or filename-derived language inference is
forbidden. Multiple input videos are allowed. There are no joins or language
versions. All video, audio and WebVTT subtitle playlists are advertised in one
`hls/master.m3u8` per movie/episode. The downloadable `master.mkv` contains
all original codec streams, copied without transcoding.

## Usage

```sh
python main.py --content-type movie --imdb tt1234567 --rating PG \
  --movie-name 'Example' --output ./cdn \
  -v english.mkv -vl english -v japanese.mkv -vl japanese \
  -a english.mka -al english -a japanese.mka -al japanese \
  -s english.srt -sl english
```

```sh
python main.py --content-type anime --anime-imdb tt2222222 \
  --imdb tt3333333 --season 1 --episode 2 --rating TV-14 \
  --show-name 'Example anime' --episode-name 'Episode 2' --output ./cdn \
  -v japanese.mkv -vl ja -a japanese.mka -al ja -s english.srt -sl en
```

Use `--show-imdb` instead of `--anime-imdb` for shows. Each episode uses its
own IMDb directory. Series metadata is in `show_metadata/<series-id>.json` or
`anime_metadata/<series-id>.json` and includes episode ratings and season/episode
mappings; `global.json` indexes the series and its highest episode rating.

`--add` retains all existing tracks even when languages repeat. `--replace`
removes existing tracks whose type and language match any new input. On an existing
IMDb, one of these flags is required. Inputs can be added incrementally.
Tracks get stable numbered names (for example `english-01`).

Structure: `<imdb>/master.mkv`, `<imdb>/hls/master.m3u8`,
`<imdb>/hls/tracks.json`, `<imdb>/hls/<language>/video/<track>/<height>p/`,
`<imdb>/hls/<language>/audio/<track>/`, and
`<imdb>/hls/subtitles/<track>.vtt` plus its playlist.

Video renditions follow the existing 2160, 1440, 1080, 720, 480, 360, 240,
144 ladder, never upscale and never include a duplicate original-quality encode.
2160/1440 use CRF 23; 1080/720 use CRF 25; smaller sizes retain established
bitrate/maxrate/bufsize settings. Audio is not quality-coupled with video.
Lossless audio is encoded to FLAC for HLS, while the MKV copies original streams.
Browser compatibility with FLAC fMP4 HLS varies; the command fails if FFmpeg
cannot mux a requested codec rather than silently applying lossy transcoding.
Every HLS subtitle is WebVTT; bitmap inputs are rejected.

**Player caution:** Standard HLS clients may not treat multiple language-labelled
video variants as independent source selectors. The master advertises TYPE=VIDEO rendition groups by quality, plus separate
AUDIO and SUBTITLES groups. Not all HLS players support switching TYPE=VIDEO
renditions. WBC still needs verification and likely custom source-stable ABR
selection.
Publishing an update does not provide transaction rollback for a power loss.
Always stage critical CDN updates and keep a backup.

Requirements: Python 3.10+, ffmpeg, ffprobe, PyYAML, langcodes.
```sh
python -m pip install -r requirements.txt
```
