import os
import re
import tempfile
import fcntl
import argparse
import json
import math
import shutil
import subprocess
import yaml
from pathlib import Path
from reader import Reader, normalize_track_language, language_display_name
from PIL import Image
AUDIO_BITRATE_PER_CHANNEL = {
    144: 80_000,
    240: 96_000,
    360: 96_000,
    480: 128_000,
    720: 128_000,
    1080: 128_000,
    1440: 320_000,
    2160: 320_000,
}
BITRATE_SETTINGS = {
    480: {
        "bitrate": 1_000_000,
        "maxrate": 1_250_000,
        "bufsize": 2_000_000,
    },
    360: {
        "bitrate": 600_000,
        "maxrate": 750_000,
        "bufsize": 1_200_000,
    },
    240: {
        "bitrate": 300_000,
        "maxrate": 375_000,
        "bufsize": 600_000,
    },
    144: {
        "bitrate": 150_000,
        "maxrate": 190_000,
        "bufsize": 300_000,
    },
}
CRF_SETTINGS = {
    720: 25,
    1080: 25,
    1440: 23,
    2160: 23,
}
RATING_PRIORITY = {
    "TV-Y": 0,
    "G": 1,
    "TV-Y7": 2,
    "TV-Y7-FV": 3,
    "TV-G": 4,
    "PG": 5,
    "TV-PG": 6,
    "PG-13": 7,
    "TV-14": 8,
    "R": 9,
    "TV-MA": 10,
    "NC-17": 11,
    "NR": 12,
}
def get_subtitle_display_name(subtitle):
    display_name = (
        subtitle.get("language_name")
        or get_language_name(
            subtitle["language"]
        )
    )

    title = subtitle.get("title")

    if title:
        normalized_title = title.strip()

        if (
            normalized_title
            and normalized_title.lower()
            not in display_name.lower()
        ):
            display_name = (
                f"{display_name} ({normalized_title})"
            )

    elif subtitle.get("hearing_impaired"):
        display_name = (
            f"{display_name} (SDH)"
        )

    if subtitle.get("forced"):
        if "forced" not in display_name.lower():
            display_name = (
                f"{display_name} (Forced)"
            )

    return display_name
def escape_hls_attribute(value):
    # HLS quoted strings cannot contain a literal double quote or a line break.
    # Backslash-escaping quotes is not defined by HLS; use a readable apostrophe.
    return " ".join(str(value).replace('"', "'").split()).strip()
def get_language_name(language):
    return language_display_name(normalize_track_language(language))
def write_vtt_playlist(
    playlist_path,
    vtt_filename,
    duration,
):
    target_duration = math.ceil(duration)

    content = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        f"#EXT-X-TARGETDURATION:{target_duration}\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        f"#EXTINF:{duration:.3f},\n"
        f"{vtt_filename}\n"
        "#EXT-X-ENDLIST\n"
    )

    with open(
        playlist_path,
        "w",
        encoding="utf-8",
    ) as file:
        file.write(content)
def get_media_duration(source_path):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )

    return float(result.stdout.strip())
def extract_subtitles(
    source_path,
    subtitle_tracks,
    paths,
    duration,
    skip_unsupported=False,
):
    subtitle_outputs = []

    for subtitle in subtitle_tracks:
        index = subtitle["index"]
        language = subtitle["language"]

        bitmap_codecs = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "dvb_teletext", "arib_caption"}
        if subtitle.get("codec") in bitmap_codecs:
            message = f"Subtitle {index} ({subtitle.get('codec')}) needs OCR or a specialist converter before WebVTT export."
            if skip_unsupported:
                print(f"[WARNING] {message} Explicitly skipped; original file still contains it.")
                continue
            raise ValueError(message + " Use --skip-unsupported-subtitles only if you want to omit it from HLS.")
        subtitle_name = f"subtitle-{index:03d}"

        vtt_path = (
            paths["vtt"]
            / f"{subtitle_name}.vtt"
        )

        playlist_path = (
            paths["vtt"]
            / f"{subtitle_name}-playlist.m3u8"
        )

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-map",
            subtitle.get("stream_selector", f"0:s:{index}"),
            "-c:s",
            "webvtt",
            str(vtt_path),
        ]

        print(
            f"[INFO] Extracting subtitle "
            f"{index} ({language})"
        )

        subprocess.run(
            command,
            check=True,
        )

        text = vtt_path.read_text(encoding="utf-8-sig")
        if not text.startswith("WEBVTT"):
            raise ValueError(f"Subtitle {index} did not produce valid WebVTT.")
        text = text.replace("WEBVTT", "WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:90000", 1)
        vtt_path.write_text(text, encoding="utf-8")
        write_vtt_playlist(
            playlist_path,
            vtt_path.name,
            duration,
        )

        subtitle_output = dict(subtitle)

        subtitle_output.update({
            "vtt_path": vtt_path,
            "playlist_path": playlist_path,
        })

        subtitle_outputs.append(
            subtitle_output
        )

    return subtitle_outputs
def get_segment_duration(segment_path):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(segment_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )

    return float(result.stdout.strip())
def calculate_rendition_bitrates(rendition):
    segment_paths = sorted(
        rendition["rendition_root"].glob(
            "*-segment-*.ts"
        )
    )

    if not segment_paths:
        raise ValueError(
            f"[ERROR] No HLS segments found for "
            f"{rendition['name']}"
        )

    peak_bitrate = 0
    total_bits = 0
    total_duration = 0

    for segment_path in segment_paths:
        duration = get_segment_duration(
            segment_path
        )

        if duration <= 0:
            continue

        size_bits = (
            segment_path.stat().st_size * 8
        )

        segment_bitrate = (
            size_bits / duration
        )

        if segment_bitrate > peak_bitrate:
            peak_bitrate = segment_bitrate

        total_bits += size_bits
        total_duration += duration

    if total_duration <= 0:
        raise ValueError(
            f"[ERROR] Could not measure bitrate for "
            f"{rendition['name']}"
        )

    average_bitrate = (
        total_bits / total_duration
    )

    return (
        math.ceil(peak_bitrate),
        math.ceil(average_bitrate),
    )
def measure_renditions(renditions):
    for rendition in renditions:
        peak_bitrate, average_bitrate = (
            calculate_rendition_bitrates(
                rendition
            )
        )

        rendition["measured_peak_bitrate"] = (
            peak_bitrate
        )

        rendition["measured_average_bitrate"] = (
            average_bitrate
        )

        print(
            f"[INFO] {rendition['name']}: "
            f"peak={peak_bitrate / 1000:.0f}k, "
            f"average={average_bitrate / 1000:.0f}k"
        )
def relative_hls_path(path, hls_root):
    return os.path.relpath(
        path,
        hls_root,
    ).replace(os.sep, "/")
def apply_track_overrides(reader, args):
    """Overrides use zero-based indices within audio or subtitle streams."""
    for kind, tracks in (("audio", reader.audio_tracks), ("subtitle", reader.subtitle_tracks)):
        for field in ("language", "title"):
            for value in getattr(args, f"{kind}_{field}", None) or []:
                index_text, separator, replacement = value.partition("=")
                if not separator or not index_text.isdigit() or int(index_text) >= len(tracks):
                    raise ValueError(f"--{kind}-{field}: use an existing zero-based index, e.g. 0=eng")
                track = tracks[int(index_text)]
                if field == "language":
                    tag = normalize_track_language(replacement)
                    if tag == "und" and replacement.strip().lower() != "und":
                        raise ValueError("Use a language tag such as en, yue, pt-BR or x-custom; put free text in --audio-title/--subtitle-title.")
                    track.update(language=tag, language_region=tag, language_short=tag.split('-')[0],
                                 language_name=language_display_name(tag))
                else:
                    track["title"] = replacement
    default = getattr(args, "default_audio", None)
    if default is not None:
        if not 0 <= default < len(reader.audio_tracks):
            raise ValueError("--default-audio must name an existing zero-based audio track.")
        for track in reader.audio_tracks:
            track["default"] = track["index"] == default


def hls_timestamp_options():
    # All separate encodes keep the same input timeline. A fixed positive offset
    # leaves room for AAC encoder priming/B-frame DTS without per-stream shifts.
    return ["-avoid_negative_ts", "disabled", "-output_ts_offset", "1",
            "-hls_segment_options", "mpegts_copyts=1"]


def clean_segments(folder, pattern):
    # Only generated segments for this rendition; no recursive user-file deletion.
    for old in folder.glob(pattern):
        old.unlink()


def encode_audio_renditions(source_path, tracks, renditions, paths, args):
    """Encode every input audio stream; reuse identical settings across qualities."""
    cache = {}
    groups = {}
    for rendition in renditions:
        if not tracks:
            continue
        outputs = []
        for track in tracks:
            settings = choose_audio_settings(track["channels"], rendition["height"], rendition["original"])
            key = (track["index"], settings["channels"], settings["bitrate"])
            if key not in cache:
                name = f'track-{key[0]:03d}-{key[1]}ch-{key[2]}'
                root = paths["hls"] / "audio" / name
                root.mkdir(parents=True, exist_ok=True)
                clean_segments(root, "audio-segment-*.ts")
                playlist = root / "audio-playlist.m3u8"
                command = ["ffmpeg", "-y", "-copyts", "-start_at_zero", "-i", str(source_path),
                           "-map", track["stream_selector"], "-vn", "-sn", "-dn",
                           "-c:a", "aac", "-profile:a", "aac_low", "-ar", "48000",
                           "-ac", str(settings["channels"]), "-b:a", str(settings["bitrate"]),
                           "-map_metadata", "-1", "-metadata:s:a:0", f'language={track["language"]}',
                           "-f", "hls", "-hls_time", "10", "-hls_playlist_type", "vod",
                           "-hls_list_size", "0", "-hls_segment_type", "mpegts",
                           *hls_timestamp_options(), "-hls_segment_filename", str(root / "audio-segment-%05d.ts"),
                           str(playlist)]
                if args.nice is not None:
                    command = ["nice", "-n", str(args.nice), *command]
                print(f'[INFO] Encoding audio {track["index"]}: {get_audio_display_name(track)} ({settings["channels"]} channels)')
                subprocess.run(command, check=True)
                output = dict(track, name=name, playlist_path=playlist, rendition_root=root,
                              output_channels=settings["channels"], output_bitrate=settings["bitrate"])
                peak, average = calculate_rendition_bitrates(output)
                output.update(measured_peak_bitrate=peak, measured_average_bitrate=average)
                cache[key] = output
            outputs.append(cache[key])
        group_key = tuple(item["name"] for item in outputs)
        existing = next((group_id for group_id, members in groups.items()
                         if tuple(item["name"] for item in members) == group_key), None)
        group_id = existing or f"audio-{len(groups)}"
        groups[group_id] = outputs
        rendition["audio_group"] = group_id
    return groups


def get_audio_display_name(track):
    label = track.get("language_name") or get_language_name(track.get("language", "und"))
    title = track.get("title")
    if title:
        label = f"{label} — {title}"
    if track.get("visual_impaired") and "description" not in label.lower():
        label += " (Audio description)"
    if track.get("commentary") and "commentary" not in label.lower():
        label += " (Commentary)"
    return label


def unique_track_names(tracks, label_function):
    used = set()
    labels = []
    for track in tracks:
        base = escape_hls_attribute(label_function(track)) or "Undetermined"
        label = base
        suffix = 2
        while label in used:
            label = f"{base} ({suffix})"
            suffix += 1
        used.add(label)
        labels.append(label)
    return labels


def default_track_index(tracks):
    return next((i for i, track in enumerate(tracks) if track.get("default")), 0)


def read_video_codec(rendition):
    """Read the actual H.264 SPS profile/compatibility/level for CODECS."""
    segment = next(iter(sorted(rendition["rendition_root"].glob("*-segment-*.ts"))))
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=extradata", "-show_data", "-of", "json", str(segment)],
                            check=True, capture_output=True, text=True)
    streams = json.loads(result.stdout).get("streams", [])
    dump = streams[0].get("extradata", "") if streams else ""
    raw = bytearray()
    for line in dump.splitlines():
        if ": " in line:
            hex_part = line.split(": ", 1)[1].split("  ", 1)[0]
            raw.extend(bytes.fromhex(hex_part))
    match = re.search(b"\x00\x00\x01\x67(.{3})", bytes(raw), re.S)
    if not match:
        raise ValueError("Could not determine encoded H.264 codec parameters.")
    return "avc1." + match.group(1).hex()


def write_tracks_metadata(reader, audio_groups, subtitles, paths):
    def plain(track):
        return {key: relative_hls_path(value, paths["hls"]) if isinstance(value, Path) else value
                for key, value in track.items() if key != "rendition_root"}
    data = {"version": 1, "audio": reader.audio_tracks, "subtitles": reader.subtitle_tracks,
            "audio_groups": {key: [plain(item) for item in members] for key, members in audio_groups.items()},
            "published_subtitles": [plain(track) for track in subtitles], "timestamp_offset_seconds": 1}
    paths["hls"].joinpath("tracks.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_master_playlist(renditions, subtitles, paths, audio_groups=None):
    audio_groups = audio_groups or {}
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS"]
    for group_id, tracks in audio_groups.items():
        default = default_track_index(tracks)
        labels = unique_track_names(tracks, get_audio_display_name)
        seen = set()
        # Process the default first when deciding which duplicate-language member
        # should auto-select; manual selection still exposes every track.
        auto = set()
        for index in [default, *(i for i in range(len(tracks)) if i != default)]:
            track = tracks[index]
            signature = (track["language"], bool(track.get("visual_impaired")), bool(track.get("hearing_impaired")))
            if signature not in seen:
                auto.add(index); seen.add(signature)
        for index, track in enumerate(tracks):
            attrs = ["TYPE=AUDIO", f'GROUP-ID="{group_id}"', f'NAME="{labels[index]}"',
                     f'LANGUAGE="{escape_hls_attribute(track["language"])}"',
                     f'DEFAULT={"YES" if index == default else "NO"}',
                     f'AUTOSELECT={"YES" if index in auto else "NO"}',
                     f'CHANNELS="{track["output_channels"]}"']
            characteristics = []
            if track.get("visual_impaired"):
                characteristics.append("public.accessibility.describes-video")
            if track.get("hearing_impaired"):
                characteristics.append("public.accessibility.describes-music-and-sound")
            if characteristics:
                attrs.append('CHARACTERISTICS="' + ','.join(characteristics) + '"')
            attrs.append(f'URI="{escape_hls_attribute(relative_hls_path(track["playlist_path"], paths["hls"]))}"')
            lines.append("#EXT-X-MEDIA:" + ",".join(attrs))
    if subtitles:
        default = default_track_index(subtitles)
        labels = unique_track_names(subtitles, get_subtitle_display_name)
        seen = set()
        auto = set()
        for index in [default, *(i for i in range(len(subtitles)) if i != default)]:
            track = subtitles[index]
            signature = (track["language"], bool(track.get("forced")), bool(track.get("hearing_impaired")))
            if signature not in seen:
                auto.add(index); seen.add(signature)
        for index, track in enumerate(subtitles):
            attrs = ["TYPE=SUBTITLES", 'GROUP-ID="subtitles"', f'NAME="{labels[index]}"',
                     f'LANGUAGE="{escape_hls_attribute(track["language"])}"',
                     f'DEFAULT={"YES" if index == default else "NO"}',
                     f'AUTOSELECT={"YES" if index in auto else "NO"}',
                     f'FORCED={"YES" if track.get("forced") else "NO"}']
            if track.get("hearing_impaired"):
                attrs.append('CHARACTERISTICS="public.accessibility.transcribes-spoken-dialog,public.accessibility.describes-music-and-sound"')
            attrs.append(f'URI="{escape_hls_attribute(relative_hls_path(track["playlist_path"], paths["hls"]))}"')
            lines.append("#EXT-X-MEDIA:" + ",".join(attrs))
    for rendition in sorted(renditions, key=lambda item: item["height"]):
        audio = audio_groups.get(rendition.get("audio_group"), [])
        peak = rendition["measured_peak_bitrate"] + max((item["measured_peak_bitrate"] for item in audio), default=0)
        average = rendition["measured_average_bitrate"] + max((item["measured_average_bitrate"] for item in audio), default=0)
        codec = read_video_codec(rendition)
        if audio:
            codec += ",mp4a.40.2"
        attrs = [f"BANDWIDTH={peak}", f"AVERAGE-BANDWIDTH={average}",
                 f'RESOLUTION={rendition["width"]}x{rendition["height"]}',
                 f'FRAME-RATE={rendition["fps_num"] / rendition["fps_den"]:.3f}', f'CODECS="{codec}"']
        if audio:
            attrs.append(f'AUDIO="{rendition["audio_group"]}"')
        if subtitles:
            attrs.append('SUBTITLES="subtitles"')
        lines += ["#EXT-X-STREAM-INF:" + ",".join(attrs), relative_hls_path(rendition["playlist_path"], paths["hls"])]
    # Publish the new manifest only after every requested stream has succeeded.
    temporary = paths["master"].with_suffix(".m3u8.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, paths["master"])
    print(f'[INFO] Wrote master playlist: {paths["master"]}')
def is_episode_content(content_type):
    return content_type.lower() in ("show", "anime")


def normalize_language(value):
    """Return a safe, lowercase language directory name; no language means legacy."""
    if value is None:
        return None
    language = value.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", language):
        raise ValueError("Language must be a name/code such as japanese, english, ja, or pt-br.")
    return language


def build_output_paths(source_path, args):
    language = normalize_language(getattr(args, "language", None))
    if language and not is_episode_content(args.content_type):
        raise ValueError("--language currently applies to show/anime episodes only.")
    if not re.fullmatch(r"(?:tt)?[0-9]+", str(args.imdb)):
        raise ValueError("Supply a numeric IMDb ID, optionally prefixed with tt.")
    if is_episode_content(args.content_type):
        if args.episode is None or args.episode < 0:
            raise ValueError("Show/anime input requires a non-negative --episode.")
        if args.season is not None and args.season < 0:
            raise ValueError("Season must be non-negative.")

    with open("./config.yml", "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    root = Path(config["output"]) / str(args.imdb)
    source_path = Path(source_path)
    extension = source_path.suffix

    if is_episode_content(args.content_type):
        season_root = root / f"s{args.season}" if args.season is not None else root
        original_root = season_root / language if language else season_root
        hls_parent = season_root / "hls"
        if language:
            hls_parent = hls_parent / language
        original_path = original_root / f"e{args.episode}{extension}"
        hls_root = hls_parent / f"e{args.episode}"
    else:
        original_path = root / f"{args.imdb}{extension}"
        hls_root = root / "hls"

    paths = {
        "root": root,
        "original": original_path,
        "hls": hls_root,
        "vtt": hls_root / "vtt",
        "master": hls_root / "master.m3u8",
    }
    for directory in (original_path.parent, hls_root, paths["vtt"]):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def copy_original(source_path, output_path):
    # Allow rerendering an original that is already in the output tree.
    if Path(source_path).resolve() == Path(output_path).resolve():
        return
    print(f"[INFO] Copying original to {output_path}")
    shutil.copy2(source_path, output_path)


def highest_rating(*values):
    ratings = [str(value).upper() for value in values if value]
    for rating in ratings:
        if rating not in RATING_PRIORITY:
            raise ValueError(f"Unknown content rating: {rating}")
    return max(ratings, key=RATING_PRIORITY.get) if ratings else "NR"


def language_container(container, language):
    """Migrate the one-language shorthand without losing its episode map."""
    old_language = container.pop("language", None)
    if old_language:
        old_language = normalize_language(old_language)
        variants = container.setdefault("languages", {})
        existing = variants.setdefault(old_language, {"language": old_language})
        existing["episodes"] = {**container.pop("episodes", {}), **existing.get("episodes", {})}
        for key in ("rating", "year"):
            if key in container:
                existing.setdefault(key, container[key])
    if language:
        return container.setdefault("languages", {}).setdefault(
            language, {"language": language, "episodes": {}}
        )
    return container


def write_content_json(paths, args):
    language = normalize_language(getattr(args, "language", None))
    episodic = is_episode_content(args.content_type)
    if language and not episodic:
        raise ValueError("--language currently applies to show/anime episodes only.")
    if episodic:
        filename = f"{args.content_type}.json"
    else:
        category = get_text_rating(args.rating)
        filename = f"{args.content_type}{'-' + category if category else ''}.json"
    json_path = paths["root"] / filename

    # Linux advisory lock covers the complete read/merge/write, so simultaneous
    # English and Japanese encodes cannot overwrite each other's JSON updates.
    with open(paths["root"] / ".metadata.lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if json_path.exists():
            content_data = json.loads(json_path.read_text(encoding="utf-8"))
        else:
            content_data = {}
        content_data["type"] = args.content_type

        if episodic:
            if args.show_name:
                content_data["name"] = args.show_name
            # 'season' is accepted on read; new output always uses 'seasons'.
            seasons = content_data.setdefault("seasons", content_data.pop("season", {}))
            container = seasons.setdefault(str(args.season), {}) if args.season is not None else content_data
            variant = language_container(container, language)
            variant["rating"] = highest_rating(variant.get("rating"), args.rating)
            if args.year:
                variant["year"] = str(args.year)
            episode_number = str(args.episode)
            episodes = variant.setdefault("episodes", {})
            if args.episode_name:
                previous = episodes.get(episode_number)
                if isinstance(previous, dict):
                    episodes[episode_number] = {**previous, "name": args.episode_name}
                else:
                    episodes[episode_number] = args.episode_name
            else:
                episodes.setdefault(episode_number, f"Episode {episode_number}")
            container["rating"] = highest_rating(
                container.get("rating"),
                *(item.get("rating") for item in container.get("languages", {}).values()),
            )
            content_data["rating"] = highest_rating(
                content_data.get("rating"),
                *(item.get("rating") for item in seasons.values()),
                *(item.get("rating") for item in content_data.get("languages", {}).values()),
            )
        else:
            content_data["rating"] = args.rating
            if args.movie_name:
                content_data["name"] = args.movie_name
            if args.year:
                content_data["year"] = str(args.year)

        descriptor, temporary = tempfile.mkstemp(prefix=".metadata-", suffix=".tmp", dir=paths["root"])
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(content_data, file, indent=4, ensure_ascii=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, json_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    print(f"[INFO] Wrote metadata: {json_path}")


def rendition_fps_text(rendition):
    fps_num = rendition["fps_num"]
    fps_den = rendition["fps_den"]
    fps = fps_num / fps_den

    if fps.is_integer():
        return str(int(fps))

    return f"{fps:.3f}".rstrip("0").rstrip(".")

def build_rendition_command(
    source_path,
    rendition,
    rendition_root,
    args,
):
    rendition_name = rendition_directory_name(rendition)

    playlist_path = (
        rendition_root
        / f"{rendition_name}-playlist.m3u8"
    )

    segment_pattern = (
        rendition_root
        / f"{rendition_name}-segment-%05d.ts"
    )

    fps_num = rendition["fps_num"]
    fps_den = rendition["fps_den"]

    # Pass the exact rational frame rate to FFmpeg.
    # Examples: 24000/1001, 30000/1001, 25/1
    fps_rate = f"{fps_num}/{fps_den}"

    segment_duration = 10

    # GOP size must be a whole number of frames.
    # This rounds:
    #
    #     fps_num * segment_duration / fps_den
    #
    # without converting the frame rate to a float.
    gop_size = max(
        1,
        (
            fps_num * segment_duration
            + fps_den // 2
        )
        // fps_den,
    )

    command = []

    if args.nice is not None:
        command.extend([
            "nice",
            "-n",
            str(args.nice),
        ])

    command.extend([
        "ffmpeg",
        "-y",

        "-copyts",
        "-start_at_zero",
        "-i",
        str(source_path),

        "-map",
        "0:v:0",
        "-an",

        "-c:v",
        "libx264",

        "-preset",
        "medium",

        "-pix_fmt",
        "yuv420p",

        "-vf",
        (
            f"scale="
            f"{rendition['width']}:"
            f"{rendition['height']}"
        ),

        # Exact fractional output frame rate.
        "-r",
        fps_rate,

        "-sn",
        "-dn",
    ])

    if rendition["rate_control"] == "crf":
        command.extend([
            "-crf",
            str(rendition["crf"]),
        ])

    else:
        command.extend([
            "-b:v",
            str(rendition["bitrate"]),

            "-maxrate",
            str(rendition["maxrate"]),

            "-bufsize",
            str(rendition["bufsize"]),
        ])

    command.extend([
        # Fixed GOP aligned approximately to the
        # requested 10-second segment duration.
        "-g",
        str(gop_size),

        "-keyint_min",
        str(gop_size),

        "-sc_threshold",
        "0",

        # Force keyframes using timestamps, so this
        # remains accurate for fractional frame rates.
        "-force_key_frames",
        (
            "expr:"
            f"gte(t,n_forced*{segment_duration})"
        ),

        "-f",
        "hls",

        "-hls_time",
        str(segment_duration),

        "-hls_playlist_type",
        "vod",

        "-hls_list_size",
        "0",

        "-hls_flags",
        "independent_segments",

        "-hls_segment_type",
        "mpegts",

        *hls_timestamp_options(),
        "-hls_segment_filename",
        str(segment_pattern),

        str(playlist_path),
    ])

    return command, playlist_path
def encode_renditions(source_path, renditions, paths, args):
    encoded_renditions = []

    for rendition in renditions:
        directory_name = rendition_directory_name(rendition)
        rendition_root = paths["hls"] / directory_name

        rendition_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        clean_segments(rendition_root, "*-segment-*.ts")
        command, playlist_path = build_rendition_command(
            source_path,
            rendition,
            rendition_root,
            args,
        )

        print(
            f"[INFO] Encoding "
            f"{rendition['name']} → {rendition_root}"
        )

        subprocess.run(
            command,
            check=True,
        )

        rendition["directory_name"] = directory_name
        rendition["playlist_path"] = playlist_path
        rendition["rendition_root"] = rendition_root

        encoded_renditions.append(rendition)

    return encoded_renditions
def rendition_directory_name(rendition):
    fps_text = rendition_fps_text(rendition)

    if rendition["original"]:
        return (
            f'{rendition["width"]}x'
            f'{rendition["height"]}-'
            f'{fps_text}fps-original'
        )

    return f'{rendition["height"]}p-{fps_text}fps'
def get_text_rating(rating):
    rating = rating.upper()

    if rating in ("G", "PG", "TV-Y", "TV-Y7"):
        return "kids"

    if rating in ("PG-13", "TV-Y7-FV", "TV-G", "TV-PG", "TV-14"):
        return ""

    if rating in ("R", "NC-17", "TV-MA"):
        return "mature"
    if rating in ("NR"):
        return "unrated"
    raise ValueError(f"Unknown rating: {rating}")
def audio_channel_cap_for_height(height):
    if height == 144:
        return 1

    if height <= 720:
        return 2

    # Surround sound: cap at 5.1 channels.
    return 6


def choose_audio_channels(source_channels, height, is_original):
    if not source_channels:
        return 0
    if is_original:
        return source_channels

    channel_cap = audio_channel_cap_for_height(height)

    # Never create more channels than the source has.
    return min(source_channels, channel_cap)


def choose_audio_settings(source_channels, height, is_original):
    channels = choose_audio_channels(
        source_channels,
        height,
        is_original,
    )

    if is_original:
        bitrate_per_channel = 320_000
    else:
        bitrate_per_channel = AUDIO_BITRATE_PER_CHANNEL[height]

    total_bitrate = bitrate_per_channel * channels

    return {
        "codec": "aac",
        "channels": channels,
        "bitrate_per_channel": bitrate_per_channel,
        "bitrate": total_bitrate,
    }
def choose_crf(default_crf, source_crf):
    if source_crf is None:
        return default_crf

    # Do not encode at a better CRF than the reported source.
    return max(default_crf, source_crf)
def fps_cap_for_height(height):
    if height <= 240:
        return 25

    if height <= 1080:
        return 30

    return 60


def capped_fps(source_num, source_den, cap):
    # Compare fractions without converting to a float.
    if source_num <= cap * source_den:
        return source_num, source_den

    return cap, 1
def check_path(path):
    if os.path.exists(path):
        if os.path.isfile(path):
            return True, True

        return True, False

    return False, None


def scaled_width(source_width, source_height, target_height):
    width = round(source_width * target_height / source_height)

    if width % 2 != 0:
        width -= 1

    return width


def main(path, args):
    paths = build_output_paths(path, args)
    qualities = [2160, 1440, 1080, 720, 480, 360, 240, 144]

    exists, is_file = check_path(path)
    reader = Reader(path, exists, is_file, args.mediainfo_path)

    reader.read(args)
    apply_track_overrides(reader, args)

    source_width = reader.video_width
    source_height = reader.video_height
    source_fps_num = reader.fps_num
    source_fps_den = reader.fps_den
    renditions = []

    if source_height not in qualities:
        audio_settings = choose_audio_settings(
            reader.audio_channels,
            source_height,
            True,
        )

        renditions.append({
            "name": f"{source_width}x{source_height}-original",
            "width": source_width,
            "height": source_height,
            "fps_num": source_fps_num,
            "fps_den": source_fps_den,
            "original": True,
            "rate_control": "crf",
            "crf": choose_crf(23, reader.crf_info),

            "audio_codec": audio_settings["codec"],
            "audio_channels": audio_settings["channels"],
            "audio_bitrate_per_channel": audio_settings[
                "bitrate_per_channel"
            ],
            "audio_bitrate": audio_settings["bitrate"],
        })

    for quality in qualities:
        if quality > source_height:
            continue

        width = scaled_width(
            source_width,
            source_height,
            quality,
        )

        is_original = quality == source_height

        if is_original:
            fps_num = source_fps_num
            fps_den = source_fps_den
        else:
            cap = fps_cap_for_height(quality)

            fps_num, fps_den = capped_fps(
                source_fps_num,
                source_fps_den,
                cap,
            )

        rendition = {
            "name": f"{quality}p",
            "width": width,
            "height": quality,
            "fps_num": fps_num,
            "fps_den": fps_den,
            "original": is_original,
        }

        if is_original:
            rendition["rate_control"] = "crf"
            rendition["crf"] = choose_crf(
                23,
                reader.crf_info,
            )

        elif quality >= 720:
            rendition["rate_control"] = "crf"
            rendition["crf"] = choose_crf(
                CRF_SETTINGS[quality],
                reader.crf_info,
            )

        else:
            settings = BITRATE_SETTINGS[quality]

            rendition["rate_control"] = "bitrate"
            rendition["bitrate"] = settings["bitrate"]
            rendition["maxrate"] = settings["maxrate"]
            rendition["bufsize"] = settings["bufsize"]
        audio_settings = choose_audio_settings(
            reader.audio_channels,
            quality,
            is_original,
        )

        rendition["audio_codec"] = audio_settings["codec"]
        rendition["audio_channels"] = audio_settings["channels"]
        rendition["audio_bitrate_per_channel"] = audio_settings[
            "bitrate_per_channel"
        ]
        rendition["audio_bitrate"] = audio_settings["bitrate"]
        renditions.append(rendition)
    for rendition in renditions:
        fps = rendition["fps_num"] / rendition["fps_den"]

        description = (
            f'{rendition["name"]}: '
            f'{rendition["width"]}x{rendition["height"]}, '
            f'{fps:.3f} FPS'
        )

        if rendition["rate_control"] == "crf":
            description += f', CRF {rendition["crf"]}'

        else:
            description += (
                f', bitrate={rendition["bitrate"] / 1000:.0f}k'
                f', maxrate={rendition["maxrate"] / 1000:.0f}k'
                f', bufsize={rendition["bufsize"] / 1000:.0f}k'
            )
        description += f", {len(reader.audio_tracks)} separate audio track(s)"
        print(description)
    copy_original(
        path,
        paths["original"],
    )

    encoded_renditions = encode_renditions(
        path,
        renditions,
        paths,
        args,
    )
    audio_groups = encode_audio_renditions(path, reader.audio_tracks, encoded_renditions, paths, args)
    duration = get_media_duration(path)

    subtitles = extract_subtitles(
        path,
        reader.subtitle_tracks,
        paths,
        duration,
        skip_unsupported=getattr(args, "skip_unsupported_subtitles", False),
    )

    measure_renditions(
        encoded_renditions
    )

    write_master_playlist(
        encoded_renditions,
        subtitles,
        paths,
        audio_groups,
    )
    write_tracks_metadata(reader, audio_groups, subtitles, paths)
    
    if args.cover_art is not None:
        image_exists, image_isfile = check_path(
            args.cover_art
        )

        if not image_exists:
            raise ValueError(
                "[ERROR] Cover art does not exist."
            )

        if not image_isfile:
            raise ValueError(
                "[ERROR] Cover art path is not a file."
            )

        cover_output = (
            paths["root"]
            / f"{args.imdb}.png"
        )

        with Image.open(args.cover_art) as image:
            # Preserve transparency where possible.
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA")

            image.save(
                cover_output,
                format="PNG",
                optimize=True,
                compress_level=9,
            )

        print(
            f"[INFO] Saved cover art: "
            f"{cover_output}"
        )
    # Publish episode metadata only after media, subtitles and cover succeed.
    write_content_json(paths, args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Lapis, a media digestion pipeline"
    )

    parser.add_argument(
        "--suppress-warning",
        "-sw",
        action="store_true",
        help="Suppress warnings",
    )

    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Width of the video",
    )

    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="Height of the input video",
    )

    parser.add_argument(
        "--input-video-codec",
        "-ivc",
        type=str,
        default=None,
        help="Codec of the video",
    )

    parser.add_argument(
        "--input-video-bitrate",
        "-ivb",
        type=int,
        default=None,
        help="Input video bitrate",
    )

    parser.add_argument(
        "--input-target-video-bitrate",
        "-itvb",
        type=int,
        default=None,
        help="Input target video bitrate",
    )

    parser.add_argument(
        "--input-fps-den",
        "-ifpsd",
        type=int,
        default=None,
        help="Input frame-rate denominator",
    )

    parser.add_argument(
        "--input-fps-num",
        "-ifpsn",
        type=int,
        default=None,
        help="Input frame-rate numerator",
    )
    parser.add_argument(
        "--input-crf",
        type=float,
        default=None,
        help="Override detected CRF",
    )
    parser.add_argument("--content-type", required=True, choices=["movie", "ova", "anime", "show"])
    parser.add_argument(
        "-r", "--rating", required=True,
        choices=["G", "PG", "PG-13", "R", "NC-17", "TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG", "TV-14", "TV-MA", "NR"],
        help="content rating (g, pg, pg-13, r, nc-17, tv-y, tv-y7, tv-y7-fv, tv-g, tv-pg, tv-14, tv-ma, NR (not rated))"
    )
    parser.add_argument("--episode", type=int)
    parser.add_argument(
        "--input",
        default="./test.mkv",
        help="Path to the input media file",
    )
    parser.add_argument("--movie-name", type=str, default=None)
    parser.add_argument("--year")
    parser.add_argument("--show-name", type=str, default=None)
    parser.add_argument("--episode-name", type=str, default=None)
    parser.add_argument("--imdb", required=True, help="IMDB ID")
    parser.add_argument("--cover-art", type=str, default=None)
    parser.add_argument("--nice", type=int, help="Nice value for ubuntu/linux to use for managing")
    parser.add_argument("--season", type=int)
    parser.add_argument(
        "--language", type=normalize_language, default=None,
        help="Show/anime version and directory name, e.g. japanese or english. Omit for the legacy layout.",
    )
    for kind in ("audio", "subtitle"):
        parser.add_argument(f"--{kind}-language", action="append", default=[], metavar="INDEX=TAG",
                            help="Override a zero-based track language; repeat for more tracks. Does not translate audio/text.")
        parser.add_argument(f"--{kind}-title", action="append", default=[], metavar="INDEX=TITLE",
                            help="Override a zero-based track display title; Unicode names are supported.")
    parser.add_argument("--default-audio", type=int, default=None, metavar="INDEX",
                        help="Zero-based audio track to play by default.")
    parser.add_argument("--skip-unsupported-subtitles", action="store_true",
                        help="Explicitly omit image/specialist subtitle formats from HLS; originals remain intact.")
    parser.add_argument("--mediainfo-path", "-MIP", type=str, default=None)
    args = parser.parse_args()
    
    main(args.input, args)
