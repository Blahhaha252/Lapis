import os
import argparse
from reader import Reader
import json
import subprocess

def check_path(path):
    if os.path.exists(path):
        if os.isfile(path):
            return True, True
        else:
            return True, False
    else:
        return False, None

def main(path):
    exist, is_file = check_path(path)
    reader = Reader(path, exists, is_file)
    try:
        reader.read()
    except Exception as e:
        print(e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lapis, a media digestion pipeline")
    parser.add_argument("--supress-warning", "-sw", action="store_true", help="Supress warnings")
    parser.add_argument("--width", type=int, default=None, help="Width of the video")
    parser.add_argument("--height", type=int, default=None, help="Height of the input video")
    parser.add_argument("--input-video-codec", "-ivc" type=str, default=None, help="Codec of the video")
    parser.add_argument("--input-video-bitrate", "--ivb", type=int, default=None, help="Input video bitrate")
    parser.add_argument("--input-target-video-bitrate", "--itvb", type=int, default=None, help="Input video target bitrate")
    parser.add_argument()
    
    args = parser.parse_args()
    path = "./test.mkv"
    main(path)
    