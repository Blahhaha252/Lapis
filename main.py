import os
import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path
from reader import Reader
from PIL import Image as img
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
LANGUAGE_NAMES = {
    "en": "English",
    "eng": "English",

    "es": "Spanish",
    "spa": "Spanish",

    "fr": "French",
    "fra": "French",
    "fre": "French",

    "de": "German",
    "deu": "German",
    "ger": "German",

    "it": "Italian",
    "ita": "Italian",

    "pt": "Portuguese",
    "por": "Portuguese",

    "ja": "Japanese",
    "jpn": "Japanese",

    "ko": "Korean",
    "kor": "Korean",

    "zh": "Chinese",
    "zho": "Chinese",
    "chi": "Chinese",

    "ru": "Russian",
    "rus": "Russian",
}
def get_language_name(language):
    return LANGUAGE_NAMES.get(
        language.lower(),
        language,
    )
def write_vtt_playlist(
    playlist_path,
    vtt_filename,
    duration,
):
    target_duration = math.ceil(duration)

    content = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
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
):
    subtitle_outputs = []

    for subtitle in subtitle_tracks:
        index = subtitle["index"]
        language = subtitle["language"]

        subtitle_name = f"{language}-{index}"

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
            f"0:s:{index}",
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

        write_vtt_playlist(
            playlist_path,
            vtt_path.name,
            duration,
        )

        subtitle_outputs.append({
            "index": index,
            "language": language,
            "vtt_path": vtt_path,
            "playlist_path": playlist_path,
        })

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
def write_master_playlist(
    renditions,
    subtitles,
    paths,
):
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]

    subtitle_group = None

    if subtitles:
        subtitle_group = "subtitles"

        for subtitle_number, subtitle in enumerate(
            subtitles
        ):
            language = subtitle["language"]

            display_name = get_language_name(
                language
            )

            subtitle_uri = relative_hls_path(
                subtitle["playlist_path"],
                paths["hls"],
            )

            if subtitle_number == 0:
                default_value = "YES"
            else:
                default_value = "NO"

            lines.append(
                "#EXT-X-MEDIA:"
                "TYPE=SUBTITLES,"
                f'GROUP-ID="{subtitle_group}",'
                f'NAME="{display_name}",'
                f'LANGUAGE="{language}",'
                f"DEFAULT={default_value},"
                "AUTOSELECT=YES,"
                f'URI="{subtitle_uri}"'
            )

    sorted_renditions = sorted(
        renditions,
        key=lambda item: item["height"],
    )

    for rendition in sorted_renditions:
        fps = (
            rendition["fps_num"]
            / rendition["fps_den"]
        )

        playlist_uri = relative_hls_path(
            rendition["playlist_path"],
            paths["hls"],
        )

        attributes = [
            (
                "BANDWIDTH="
                f'{rendition["measured_peak_bitrate"]}'
            ),
            (
                "AVERAGE-BANDWIDTH="
                f'{rendition["measured_average_bitrate"]}'
            ),
            (
                "RESOLUTION="
                f'{rendition["width"]}x'
                f'{rendition["height"]}'
            ),
            f"FRAME-RATE={fps:.3f}",
            'CODECS="avc1.640028,mp4a.40.2"',
        ]

        if subtitle_group:
            attributes.append(
                f'SUBTITLES="{subtitle_group}"'
            )

        lines.append(
            "#EXT-X-STREAM-INF:"
            + ",".join(attributes)
        )

        lines.append(playlist_uri)

    lines.append("")

    with open(
        paths["master"],
        "w",
        encoding="utf-8",
    ) as file:
        file.write("\n".join(lines))

    print(
        f"[INFO] Wrote master playlist: "
        f'{paths["master"]}'
    )
def is_episode_content(content_type):
    return content_type.lower() in ("show", "anime")


def build_output_paths(source_path, args):
    source_path = Path(source_path)

    root = Path(str(args.imdb))
    root.mkdir(parents=True, exist_ok=True)

    extension = source_path.suffix

    if is_episode_content(args.content_type):
        episode_name = f"e{args.episode}"
        original_name = f"{episode_name}{extension}"

        if args.season is not None:
            season_root = root / f"s{args.season}"

            original_path = season_root / original_name
            hls_root = season_root / "hls" / episode_name

        else:
            original_path = root / original_name
            hls_root = root / "hls" / episode_name

    else:
        original_name = f"{args.imdb}{extension}"
        original_path = root / original_name
        hls_root = root / "hls"

    vtt_root = hls_root / "vtt"
    master_path = hls_root / "master.m3u8"

    original_path.parent.mkdir(parents=True, exist_ok=True)
    hls_root.mkdir(parents=True, exist_ok=True)
    vtt_root.mkdir(parents=True, exist_ok=True)

    return {
        "root": root,
        "original": original_path,
        "hls": hls_root,
        "vtt": vtt_root,
        "master": master_path,
    }
def copy_original(source_path, output_path):
    print(f"[INFO] Copying original to {output_path}")

    shutil.copy2(
        source_path,
        output_path,
    )
def write_content_json(paths, args):
    text_rating = get_text_rating(args.rating)

    if text_rating:
        json_filename = (
            f"{args.content_type}-{text_rating}.json"
        )
    else:
        json_filename = f"{args.content_type}.json"

    json_path = paths["root"] / json_filename

    # Movie-like content gets a simple JSON file.
    if args.content_type not in ("show", "anime"):
        content_data = {
            "rating": args.rating,
            "type": args.content_type,
        }

    # Shows and anime keep episode information.
    else:
        if json_path.exists():
            with open(
                json_path,
                "r",
                encoding="utf-8",
            ) as file:
                content_data = json.load(file)
        else:
            content_data = {
                "rating": args.rating,
                "type": args.content_type,
                "episodes": {},
            }

        # Always refresh these values.
        content_data["rating"] = args.rating
        content_data["type"] = args.content_type

        if args.show_name:
            content_data["name"] = args.show_name

        if "episodes" not in content_data:
            content_data["episodes"] = {}

        if args.episode and args.episode_name:
            content_data["episodes"][args.episode] = (
                args.episode_name
            )

    with open(
        json_path,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            content_data,
            file,
            indent=4,
        )

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
        "-i",
        str(source_path),

        "-map",
        "0:v:0",

        "-map",
        "0:a:0",

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

        "-r",
        f"{fps_num}/{fps_den}",

        "-c:a",
        rendition["audio_codec"],

        "-ac",
        str(rendition["audio_channels"]),

        "-b:a",
        str(rendition["audio_bitrate"]),
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

    segment_duration = 6

    fps = fps_num / fps_den
    gop_size = max(1, round(fps * segment_duration))

    command.extend([
        "-g",
        str(gop_size),

        "-keyint_min",
        str(gop_size),

        "-sc_threshold",
        "0",

        "-force_key_frames",
        f"expr:gte(t,n_forced*{segment_duration})",

        "-f",
        "hls",

        "-hls_time",
        str(segment_duration),

        "-hls_playlist_type",
        "vod",

        "-hls_list_size",
        "0",

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

    raise ValueError(f"Unknown rating: {rating}")
def audio_channel_cap_for_height(height):
    if height == 144:
        return 1

    if height <= 720:
        return 2

    # Surround sound: cap at 5.1 channels.
    return 6


def choose_audio_channels(source_channels, height, is_original):
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
    qualities = [2160, 1440, 1080, 720, 480, 360, 240, 144]

    exists, is_file = check_path(path)
    reader = Reader(path, exists, is_file)

    try:
        reader.read(args)
    except Exception as error:
        print(error)
        return

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
        description += (
            f', audio=AAC'
            f', channels={rendition["audio_channels"]}'
            f', audio bitrate={rendition["audio_bitrate"] / 1000:.0f}k'
            f' total'
            f' ({rendition["audio_bitrate_per_channel"] / 1000:.0f}k/ch)'
        )
        print(description)
    paths = build_output_paths(path, args)

    copy_original(
        path,
        paths["original"],
    )

    write_content_json(
        paths,
        args,
    )

    encoded_renditions = encode_renditions(
        path,
        renditions,
        paths,
        args,
    )
    duration = get_media_duration(
        path
    )

    subtitles = extract_subtitles(
        path,
        reader.subtitle_tracks,
        paths,
        duration,
    )

    measure_renditions(
        encoded_renditions
    )

    write_master_playlist(
        encoded_renditions,
        subtitles,
        paths,
    )
    
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
    parser.add_argument("--content-type", choices=["movie", "ova", "anime", "show"])
    parser.add_argument(
        "-r", "--rating", required=True,
        choices=["G", "PG", "PG-13", "R", "NC-17", "TV-Y", "TV-Y7", "TV-Y7-FV", "TV-G", "TV-PG", "TV-14", "TV-MA"],
        help="content rating (g, pg, pg-13, r, nc-17, tv-y, tv-y7, tv-y7-fv, tv-g, tv-pg, tv-14, tv-ma)"
    )
    parser.add_argument("--episode", type=int)
    parser.add_argument(
        "--input",
        default="./test.mkv",
        help="Path to the input media file",
    )
    parser.add_argument("--show-name", type=str, default=None)
    parser.add_argument("--episode-name", type=str, default=None)
    parser.add_argument("--imdb", help="IMDB ID")
    parser.add_argument("--cover-art", type=str, default=None)
    parser.add_argument("--nice", type=int, help="Nice value for ubuntu/linux to use for managing")
    parser.add_argument("--season", type=int)
    args = parser.parse_args()
    
    main(args.input, args)