import subprocess
import os
import json
import re
from pathlib import Path

try:
    import langcodes
except ImportError:
    langcodes = None


def normalize_track_language(value):
    """Accept language tags without an allowlist; missing/unusable tags become und.

    This checks tag shape, not registry membership: future/rare/private-use codes
    remain usable. Names in native scripts belong in the track title, not LANGUAGE.
    """
    text = str(value or "").strip().replace("_", "-")
    if text.lower() in ("", "unknown", "undefined", "unknown language"):
        return "und"
    if not re.fullmatch(r"(?:[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*|[ixIX](?:-[A-Za-z0-9]{1,8})+)", text):
        return "und"
    if langcodes is not None:
        try:
            return langcodes.standardize_tag(text)
        except (ValueError, LookupError):
            pass
    parts = text.lower().split("-")
    # Without the optional registry package, preserve codes and all subtags.
    for index in range(1, len(parts)):
        if len(parts[index - 1]) == 1:
            break
        if len(parts[index]) == 4 and parts[index].isalpha():
            parts[index] = parts[index].title()
        elif len(parts[index]) == 2 and parts[index].isalpha():
            parts[index] = parts[index].upper()
    return "-".join(parts)


def language_display_name(tag):
    if tag == "und":
        return "Undetermined"
    if langcodes is not None:
        try:
            return langcodes.Language.get(tag).display_name("en")
        except (ValueError, LookupError, ModuleNotFoundError):
            pass
    return tag


class Reader:
    def __init__(self, media_path, exist, is_file, media_probe_path=None):
        # stuff accessable by the main script for logical work
        self.exists = exist
        self.is_file = is_file
        self.media = media_path
        self.video_codec = None
        self.video_width = None
        self.video_height = None
        self.fps_num = None
        self.fps_den = None
        self.fps = None
        self.media_probe = media_probe_path
        self.crf_info = None
        self.audio_codec = None
        self.audio_bitrate = None
        self.audio_bitrate_mode = None
        self.audio_channels = None
        self.audio_bitrate_per_channel = None
        self.subtitle_tracks = []
        self.audio_tracks = []
        self.common_fps = [
            (23.976023976, (24000, 1001)),
            (24.0,          (24, 1)),
            (25.0,          (25, 1)),
            (29.97002997,   (30000, 1001)),
            (30.0,          (30, 1)),
            (47.952047952,  (48000, 1001)),
            (48.0,          (48, 1)),
            (50.0,          (50, 1)),
            (59.94005994,   (60000, 1001)),
            (60.0,          (60, 1)),
            (119.88011988,  (120000, 1001)),
            (120.0,         (120, 1)),
        ]
    def get_crf(self, encoded_settings):
        if not encoded_settings:
            return None

        if "rc=crf" not in encoded_settings.lower():
            return None

        for setting in encoded_settings.split("/"):
            setting = setting.strip()

            if setting.lower().startswith("crf="):
                return float(setting.split("=", 1)[1])

        return None
    def clean_metadata_value(self, value):
        if value is None:
            return None

        value = str(value).strip()

        if not value:
            return None

        return value


    def metadata_flag(self, value):
        if value is None:
            return False

        return str(value).strip().lower() in (
            "yes",
            "true",
            "1",
        )


    def get_track_metadata(self, track):
        """Use MediaInfo exclusively for source language/title/disposition metadata."""
        # Raw Language retains region/script subtags; never prefer a shortened
        # display field or infer the spoken language from the filename.
        candidates = [track.get("Language"), track.get("Language_String4"),
                      track.get("Language_String2"), track.get("Language_String3")]
        language = "und"
        for candidate in candidates:
            normalized = normalize_track_language(candidate)
            if normalized != "und":
                language = normalized
                break
        title = self.clean_metadata_value(track.get("Title"))

        def flag(name):
            extra = track.get("extra") or {}
            return any(self.metadata_flag(value) for value in (
                track.get(name), track.get(name + "_String"),
                track.get(name + "/String"), extra.get(name),
            ))

        return {
            "language": language,
            "language_short": language.split("-", 1)[0],
            "language_three": self.clean_metadata_value(track.get("Language_String3")),
            "language_region": language,
            "language_name": self.clean_metadata_value(track.get("Language_String") or track.get("Language_String1")) or language_display_name(language),
            "original_language": next((str(value) for value in candidates if value), None),
            "title": title,
            "hearing_impaired": flag("HearingImpaired") or bool(title and re.search(r"\bSDH\b", title, re.I)),
            "visual_impaired": flag("VisualImpaired"),
            "commentary": flag("Commentary") or bool(title and "commentary" in title.lower()),
            "forced": flag("Forced"),
            "default": flag("Default"),
            "metadata_source": "MediaInfo",
        }

    def get_subtitle_metadata(self, track):
        # Kept for callers using the older Reader interface; never drops a track.
        return self.get_track_metadata(track)

    def probe_media(self):
        
        if self.media_probe_path is not None:
            if os.path.exists(self.media_probe_path) and os.path.is_file(self.media_probe_path)
            bundled = self.media_probe_path
        binary = str(bundled) if bundled.is_file() else "mediainfo"
        result = subprocess.run(
            [binary, "-f", "--Output=JSON", str(self.media)],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)

    def read_streams(self, tracks=None):
        """Read source streams from the same MediaInfo report as the video.

        Map with FFmpeg's per-type ordinal (0:a:N / 0:s:N). MediaInfo IDs and
        StreamOrder are retained for diagnostics, never treated as FFmpeg IDs.
        """
        if tracks is None:
            tracks = self.probe_media().get("media", {}).get("track", [])
        self.audio_tracks = []
        self.subtitle_tracks = []

        def integer_value(track, *names):
            for name in names:
                value = str(track.get(name) or "").strip()
                # Full MediaInfo JSON uses raw numeric values. Some compound
                # audio formats expose multiple presentations separated by '/'.
                match = re.match(r"^(\d+)(?:\.\d+)?(?:\s*/|$)", value)
                if match:
                    return int(match.group(1))
            return 0

        subtitle_codecs = {
            "pgs": "hdmv_pgs_subtitle", "hdmv pgs": "hdmv_pgs_subtitle",
            "vobsub": "dvd_subtitle", "dvd subtitle": "dvd_subtitle",
            "dvb subtitle": "dvb_subtitle", "dvb subtitles": "dvb_subtitle",
            "xsub": "xsub", "teletext": "dvb_teletext", "arib std b24/b37": "arib_caption",
        }
        for track in tracks:
            kind = track.get("@type")
            if kind not in ("Audio", "Text"):
                continue
            collection = self.audio_tracks if kind == "Audio" else self.subtitle_tracks
            index = len(collection)
            metadata = self.get_track_metadata(track)
            codec = track.get("Format") or track.get("CodecID") or "unknown"
            metadata.update(index=index, stream_selector=f"0:{'a' if kind == 'Audio' else 's'}:{index}",
                            codec=subtitle_codecs.get(codec.lower(), codec) if kind == "Text" else codec,
                            format=track.get("Format"), codec_id=track.get("CodecID"),
                            track_id=track.get("ID"), stream_order=track.get("StreamOrder"))
            if kind == "Audio":
                channels = integer_value(track, "Channels", "Channel(s)")
                if channels < 1:
                    raise ValueError(f"MediaInfo audio track {index} has no readable channel count.")
                metadata.update(channels=channels, channel_layout=track.get("ChannelLayout"),
                                sample_rate=integer_value(track, "SamplingRate"),
                                bitrate=integer_value(track, "BitRate"), bitrate_mode=track.get("BitRate_Mode"))
            collection.append(metadata)
        self.audio_codec = self.audio_channels = self.audio_bitrate = None
        self.audio_bitrate_mode = self.audio_bitrate_per_channel = None
        if self.audio_tracks:
            first = self.audio_tracks[0]
            self.audio_codec = first["codec"]
            self.audio_channels = first["channels"]
            self.audio_bitrate = first["bitrate"] or None
            self.audio_bitrate_mode = first["bitrate_mode"]
            self.audio_bitrate_per_channel = self.audio_bitrate / self.audio_channels if self.audio_bitrate else None

    def read(self, args):
        # filtering paths and such
        if not self.exists:
            raise ValueError("[ERROR] Media does not appear to exist")
        if not self.is_file:
            raise ValueError("[ERROR] Currently not supporting a folder of contents")
        data = self.probe_media()
        if not data['media']['track']:
            raise ValueError(f"Expected 'media' inside of mediainfo output of {self.media}")
        tracks = data["media"]["track"]

        video_tracks = []
        audio_tracks = []
        subtitle_tracks = []

        for item in tracks:
            track_type = item.get("@type")

            if track_type == "Video":
                video_tracks.append(item)

            elif track_type == "Audio":
                audio_tracks.append(item)

            elif track_type == "Text":
                subtitle_tracks.append(item)

        if len(video_tracks) > 1:
            raise ValueError(
                f"[ERROR] Multiple video tracks are not supported. "
                f"Found {len(video_tracks)} video tracks."
            )

        if not video_tracks:
            raise ValueError("[ERROR] No video track was found.")
                            
        item = video_tracks[0]
        # metrics collecting from json data
        format_string = item.get("Format_String")
        plain_format = item.get("Format")
        height = item.get("Height")
        width = item.get("Width")
        sampled_h = item.get("Sampled_Height")
        sampled_w = item.get("Sampled_Width")
        stored_h = item.get("Stored_Height")
        stored_w = item.get("Stored_Width")
        fps_num = item.get("FrameRate_Num")
        fps_den = item.get("FrameRate_Den")
        fps = item.get("FrameRate")
        encoded_settings = item.get("Encoded_Library_Settings")
        detected_crf = self.get_crf(encoded_settings)
        if args.input_crf is not None:
            if detected_crf is not None and not args.suppress_warning:
                print(
                    "[WARNING] CRF info was provided even though the "
                    "media already exposes it. Using the provided value."
                )

            self.crf_info = float(args.input_crf)

        else:
            self.crf_info = detected_crf
        # logic for video codec information
        if args.input_video_codec:
            if (format_string or plain_format) and not args.suppress_warning:
                print("[WARNING] Video codec was provided even though the media already exposes it. Using the provided value.")
            self.video_codec = args.input_video_codec
        elif format_string:
            self.video_codec = format_string
        elif plain_format:
            self.video_codec = plain_format
        else:
            raise ValueError("[ERROR] format_string and plain_format was not found. Please pass it in")
        # logic for width and height
        
        if args.width is not None:
            if (stored_w or width or sampled_w) and not args.suppress_warning:
                print("[WARNING] Video width was provided even though the media already exposes it. Using the provided value.")
            self.video_width = int(args.width)
        elif stored_w:
            self.video_width = int(stored_w)
        elif sampled_w:
            self.video_width = int(sampled_w)
        elif width:
            self.video_width = int(width)
        else:
            raise ValueError("[ERROR] width, stored width, and sampled width was not found. Please pass it in")
        
        if args.height is not None:
            if (stored_h or height or sampled_h) and not args.suppress_warning:
                 print("[WARNING] Video  was provided even though the media already exposes it. Using the provided value.")
            self.video_height = int(args.height)
        elif stored_h:
            self.video_height = int(stored_h)
        elif sampled_h:
            self.video_height = int(sampled_h)
        elif height:
            self.video_height = int(height)
        else:
            raise ValueError("[ERROR] height, stored height, and sampled height was not found. Please pass it in")
        
        
        if (
            args.input_fps_den is not None
            and args.input_fps_num is None
        ) or (
            args.input_fps_den is None
            and args.input_fps_num is not None
        ):
            raise ValueError(
                "[ERROR] FPS denominator and numerator are required together"
            )

        elif (
            args.input_fps_den is not None
            and args.input_fps_num is not None
        ):
            if ((fps_den and fps_num) or fps) and not args.suppress_warning:
                print(
                    "[WARNING] Video fps num/den was provided even though "
                    "the media already exposes it. Using the provided value."
                )

            self.fps_num = int(args.input_fps_num)
            self.fps_den = int(args.input_fps_den)

        elif fps_den and fps_num:
            self.fps_num = int(fps_num)
            self.fps_den = int(fps_den)

        elif fps:
            flt_fps = float(fps)

            for value, (num, den) in self.common_fps:
                if abs(flt_fps - value) < 0.001:
                    self.fps_num = num
                    self.fps_den = den
                    break

            if self.fps_num is None or self.fps_den is None:
                raise ValueError(f"[ERROR] Unsupported FPS: {fps}")

        else:
            raise ValueError("[ERROR] FPS information was not found")

        self.fps = self.fps_num / self.fps_den
        self.read_streams(tracks)
