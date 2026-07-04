import subprocess
import os
import json
class Encoder:
    def __init__(self):
        self.media_path = ""
        self.config = {}
    def eat(self, media_path):
        if not os.path.exists(media_path):
           raise FileNotFoundError(f"OH NO, THE PLATE IS EMPTY!\nspecifics: file not found {media_path}")
        self.media_path = media_path
        print("eating.....\nAAAAAHHHHHHHHH")
        
    def chew(self, config):
        pass
    def digest(self):
        pass
    def crap(self):
        pass
    def wipe(self):
        pass
    def flush(self):
        pass
    def barf(self):
        pass
class Reader:
    def __init__(self, media_path):
        self.media = media_path
        if not os.path.exists(self.media):
            raise FileNotFoundError(f"File not found: {self.media}")
        self.video_codec = None
        self.video_bitrate = None
        self.video_bitrate_target = None
        self.video_bitrate_mode =  None
        self.video_width = None
        self.video_height = None
    def read(self, args):
        cmd = ["./mi/mediainfo", "-f", "--Output=JSON", f"{self.media}"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(r.stdout)
        if not data['media']['track']:
            raise ValueError(f"Expected 'media' inside of mediainfo output of {self.media}")
        for idx, item in enumerate(data['media']['track']):
            if item['@type'] == "Video":
                format_string = item.get("Format_String")
                plain_format = item.get("Format")
                height = item.get("Height")
                width = item.get("Width")
                sampled_h = item.get("Sampled_Height")
                sampled_w = item.get("Sampled_Width")
                stored_h = item.get("Stored_Height")
                stored_w = item.get("Stored_Width")
                if not format_string and not plain_format and if not args.input_video_codec:
                    raise ValueError("format_string and plain_format was not found. Please pass it in")
                if args.input_video_codec:
                    if (format_string or plain_format) and not args.suppress_warning:
                        print(
                            "[WARNING] Video codec was provided even though the media "
                            "already exposes it. Using the provided value."
                        )
                    self.video_codec = args.input_video_codec
                if format_string:
                    self.video_codec = format_string
                elif plain_format:
                    self.video_codec = plain_format
                if not stored_w and width and sampled_w:
                    raise ValueError("Width could not be determined. Please pass it in")
                if not stored_h and height and sampled_h:
                    raise ValueError("Height could not be determined. Please pass it in")
                if args.