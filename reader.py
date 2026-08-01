import subprocess
import os
import json

class Reader:
    def __init__(self, media_path, exist, is_file):
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
        self.crf_info = None
        self.audio_codec = None
        self.audio_bitrate = None
        self.audio_bitrate_mode = None
        self.audio_channels = None
        self.audio_bitrate_per_channel = None
        self.subtitle_tracks = []
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
    def get_subtitle_language(self, track):
        language = track.get("Language")

        if not language:
            return None

        language = language.strip().lower()

        if language in ("und", "unknown", "undefined", "zxx"):
            return None

        if not language.isalpha():
            return None

        if len(language) not in (2, 3): 
            return None

        return language
    def read(self, args):
        # filtering paths and such
        if not self.exists:
            raise ValueError("[ERROR] Media does not appear to exist")
        if not self.is_file:
            raise ValueError("[ERROR] Currently not supporting a folder of contents")
        cmd = ["./mi/mediainfo", "-f", "--Output=JSON", f"{self.media}"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(r.stdout)
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

        if len(audio_tracks) > 1:
            raise ValueError(
                f"[ERROR] Multiple audio tracks are not supported. "
                f"Found {len(audio_tracks)} audio tracks."
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
        if audio_tracks:
            audio = audio_tracks[0]

            audio_format_string = audio.get("Format_String")
            audio_plain_format = audio.get("Format")
            audio_bitrate = audio.get("BitRate")
            audio_bitrate_mode = audio.get("BitRate_Mode")
            audio_channels = audio.get("Channels")

            if audio_format_string:
                self.audio_codec = audio_format_string
            elif audio_plain_format:
                self.audio_codec = audio_plain_format
            else:
                raise ValueError("[ERROR] Audio codec information was not found.")

            if audio_bitrate:
                self.audio_bitrate = int(float(audio_bitrate))

            if audio_bitrate_mode:
                self.audio_bitrate_mode = audio_bitrate_mode

            if audio_channels:
                self.audio_channels = int(audio_channels)
            if self.audio_bitrate and self.audio_channels:
                self.audio_bitrate_per_channel = (
                    self.audio_bitrate / self.audio_channels
                )
        if subtitle_tracks:
            for index, subtitle in enumerate(subtitle_tracks):
                language = self.get_subtitle_language(subtitle)

                if not language:
                    raise ValueError(
                        "[ERROR] Subtitle track has a missing or invalid language."
                    )

                self.subtitle_tracks.append({
                    "index": index,
                    "language": language,
                })