from pathlib import Path
import shutil
import subprocess
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

@pytest.fixture(scope='session')
def media_tools(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg and FFprobe required')
    if shutil.which('mediainfo'):
        return shutil.which('mediainfo')
    pytest.importorskip('pymediainfo')
    # REAL MediaInfo shared library, not invented or FFprobe-derived metadata.
    helper = tmp_path_factory.mktemp('mediainfo') / 'mediainfo'
    helper.write_text(f'#!{sys.executable}\nimport sys\nfrom pymediainfo import MediaInfo\n'
                      "print(MediaInfo.parse(sys.argv[-1], output='JSON', full=True))\n")
    helper.chmod(0o755)
    return str(helper)

def ffmpeg(*arguments):
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', *map(str, arguments)],
                   check=True, capture_output=True)

@pytest.fixture(scope='session')
def media(tmp_path_factory, media_tools):
    root = tmp_path_factory.mktemp('input media')
    first, second = root / 'red video.mkv', root / 'blue video.mkv'
    english, japanese = root / 'english.m4a', root / 'japanese.acc'
    en_sub, ja_sub = root / 'english.vtt', root / 'japanese.vtt'
    for path, color, size, rate in ((first, 'red', '128x96', '24'), (second, 'blue', '160x120', '25')):
        ffmpeg('-f', 'lavfi', '-i', f'color=c={color}:s={size}:r={rate}:d=2', '-c:v', 'libx264',
               '-threads', '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', path)
    for path, freq, fmt in ((english, 440, 'ipod'), (japanese, 880, 'adts')):
        ffmpeg('-f', 'lavfi', '-i', f'sine=frequency={freq}:duration=2:sample_rate=48000',
               '-c:a', 'aac', '-threads', '1', '-f', fmt, path)
    for path, text in ((en_sub, 'Hello'), (ja_sub, 'こんにちは')):
        path.write_text(f'WEBVTT\n\n00:00:00.250 --> 00:00:01.500\n{text}\n', encoding='utf-8')
    combined = root / 'existing.mkv'
    ffmpeg('-i', first, '-i', english, '-i', japanese, '-i', en_sub,
           '-map', '0:v:0', '-map', '1:a:0', '-map', '2:a:0', '-map', '3:s:0', '-c', 'copy',
           '-metadata:s:a:0', 'language=eng', '-metadata:s:a:1', 'language=jpn', combined)
    two_videos = root / 'two-videos.mkv'
    ffmpeg('-i', first, '-i', second, '-map', '0:v:0', '-map', '1:v:0', '-c', 'copy', two_videos)
    larger = root / 'fractional.mkv'
    ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=384x288:r=24000/1001:d=2', '-c:v', 'libx264',
           '-threads', '1', '-preset', 'ultrafast', larger)
    return dict(first=first, second=second, english=english, japanese=japanese,
                en_sub=en_sub, ja_sub=ja_sub, combined=combined, two_videos=two_videos, larger=larger)
