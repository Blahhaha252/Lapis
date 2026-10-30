import json
import shutil
import subprocess
from pathlib import Path
import pytest
from unified import cli

def ffmpeg(*args):
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-nostdin","-y",*map(str,args)],check=True)

def probe(path):
    result=subprocess.run(["ffprobe","-v","error","-show_streams","-of","json",str(path)],
        capture_output=True,text=True,check=True)
    return json.loads(result.stdout)["streams"]

@pytest.fixture
def media(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg/FFprobe unavailable")
    v=tmp_path/"video.mkv"
    flac=tmp_path/"english.flac"
    aac=tmp_path/"another.m4a"
    subtitle=tmp_path/"english.srt"
    ffmpeg("-f","lavfi","-i","color=c=red:s=320x240:r=25:d=1",
           "-c:v","libx264","-pix_fmt","yuv420p","-an",v)
    ffmpeg("-f","lavfi","-i","sine=frequency=440:duration=1",
           "-c:a","flac",flac)
    ffmpeg("-f","lavfi","-i","sine=frequency=660:duration=1",
           "-c:a","aac",aac)
    subtitle.write_text("1\n00:00:00,100 --> 00:00:00,800\nHello there!\n",encoding="utf-8")
    return v,flac,aac,subtitle

def test_real_single_master_and_incremental_tracks(tmp_path,media):
    video,flac,aac,subtitle=media
    root=tmp_path/"cdn"
    base=["--content-type","movie","--imdb","tt987654","--movie-name","Test",
          "--rating","PG","--output",str(root)]
    assert cli(base+["-v",str(video),"-vl","english",
                     "-a",str(flac),"-al","english",
                     "-s",str(subtitle),"-sl","english"])==0
    content=root/"tt987654";hls=content/"hls"
    assert (hls/"master.m3u8").is_file()
    tracks=json.loads((hls/"tracks.json").read_text())["tracks"]
    assert len(tracks)==3
    renditions=next(t["renditions"] for t in tracks if t["kind"]=="video")
    assert [r["height"] for r in renditions]==[240,144]
    first_ts=next((hls/Path(renditions[0]["playlist"]).parent).glob("*.ts"))
    assert [s["codec_type"] for s in probe(first_ts)]==["video"]
    assert (hls/"subtitles"/next(t["id"] for t in tracks if t["kind"]=="subtitle")).with_suffix(".vtt").is_file()
    assert len(probe(content/"master.mkv"))==3
    assert cli(base+["--add","-a",str(aac),"-al","english"])==0
    tracks=json.loads((hls/"tracks.json").read_text())["tracks"]
    assert len([t for t in tracks if t["kind"]=="audio"])==2
    assert len(probe(content/"master.mkv"))==4
    assert cli(base+["--replace","-a",str(aac),"-al","english"])==0
    tracks=json.loads((hls/"tracks.json").read_text())["tracks"]
    assert len([t for t in tracks if t["kind"]=="audio"])==1
    assert len(probe(content/"master.mkv"))==3
    global_data=json.loads((root/"global.json").read_text())
    assert global_data["contents"][0]["rating"]=="PG"

def test_series_catalog_per_episode_highest_rating(tmp_path,media):
    video,_,_,_=media
    root=tmp_path/"cdn"
    for episode,imdb,rating in [(1,"tt101","TV-PG"),(2,"tt102","TV-MA")]:
        assert cli(["--content-type","anime","--anime-imdb","tt100",
            "--imdb",imdb,"--season","1","--episode",str(episode),
            "--show-name","Anime Test","--episode-name","E"+str(episode),
            "--rating",rating,"--output",str(root),
            "-v",str(video),"-vl","japanese"])==0
    series=json.loads((root/"anime_metadata"/"tt100.json").read_text())
    assert series["rating"]=="TV-MA"
    assert series["seasons"]["1"]["episodes"]["1"]["rating"]=="TV-PG"
    assert series["seasons"]["1"]["episodes"]["2"]["rating"]=="TV-MA"
    assert (root/"tt101"/"hls"/"master.m3u8").is_file()
    assert (root/"tt102"/"hls"/"master.m3u8").is_file()
    global_data=json.loads((root/"global.json").read_text())
    assert global_data["contents"][0]["rating"]=="TV-MA"
