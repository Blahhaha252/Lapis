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
    def __init__(self, media_path, exist, is_file):
        # stuff accessable by the main script for logical work
        self.exists = exists
        self.is_file = is_file
        self.media = media_path
        self.video_codec = None
        self.video_bitrate = None
        self.video_bitrate_target = None
        self.video_bitrate_mode =  None
        self.video_width = None
        self.video_height = None
        self.fps_num = None
        self.fps_den = None
        self.fps = None
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
        for idx, item in enumerate(data['media']['track']):
            if item['@type'] == "Video":
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
                
                if args.width:
                    if (stored_w or width or sampled_w) and not args.supress_warning:
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
                
                if args.height:
                    if (stored_h or height or sampled_h) and not args.supress_warning:
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
                
                
                if (args.input_fps_den and not args.input_fps_num) or (not args.input_fps_den and args.input_fps_num):
                    raise ValueError("[ERROR] FPS denominator and numerator are required together to use these commands")
                elif args.input_fps_den and args.fps_num:
                    if ((fps_den and fps_num) or fps):
                        print("[WARNING] Video fps num/den was provided even though the media already exposes it. Using the provided value.")
                    self.fps_num = int(args.input_fps_num)
                    self.fps_den = int(args.input_fps_den)
                elif (fps_den and fps_num):
                    self.fps_num = int(fps_num)
                    self.fps_den = int(fps_den)
                elif fps:
                    flt_fps = float(fps)
                    for value, (num, den) in self.common_fps:
                        if abs(flt_fps - value) < 0.001:
                            self.fps_num = num
                            self.fps_den = den 
                            break