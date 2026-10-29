"""Exercise separate HLS using real FFmpeg output, not just command strings."""

from array import array
import json
from pathlib import Path
import subprocess
import sys

import m3u8
import pytest

import main
from conftest import ffmpeg
from test_integration import options, probe, assert_playlists


def media_segment(playlist_path):
    playlist = m3u8.load(str(playlist_path))
    return playlist_path.parent / playlist.segments[0].uri


def assert_separate(root):
    master = m3u8.load(str(root / "master.m3u8"))
    for variant in master.playlists:
        assert variant.uri.startswith("video/")
        assert {s["codec_type"] for s in probe(media_segment(root / variant.uri))["streams"]} == {"video"}
        if variant.stream_info.audio:
            assert any(t.type == "AUDIO" and t.group_id == variant.stream_info.audio for t in master.media)
        if variant.stream_info.subtitles:
            assert any(t.type == "SUBTITLES" and t.group_id == variant.stream_info.subtitles for t in master.media)
    for track in master.media:
        segment = media_segment(root / track.uri)
        if track.type == "AUDIO":
            assert track.uri.startswith("audio/")
            assert {s["codec_type"] for s in probe(segment)["streams"]} == {"audio"}
        elif track.type == "SUBTITLES":
            assert track.uri.startswith("vtt/")
            assert segment.read_text().startswith("WEBVTT\nX-TIMESTAMP-MAP=")
    metadata = json.loads((root / "tracks.json").read_text())
    assert metadata["packaging"] == "separate-hls"
    assert metadata["master_playlist"] == "master.m3u8"
    assert {v["playlist_path"] for v in metadata["video_renditions"]} == {v.uri for v in master.playlists}
    assert not any(str(root) in json.dumps(v) for v in metadata["video_renditions"])
    return master


def reject_download(*args, **kwargs):
    raise AssertionError("HLS-only must never create or copy a combined download")


def test_hls_only_direct_sources(tmp_path, media, media_tools, monkeypatch):
    monkeypatch.setattr(main, "mux_original", reject_download)
    monkeypatch.setattr(main, "copy_original", reject_download)
    commands = []
    real_run = subprocess.run

    def observe(command, **kwargs):
        if command[0] == "ffmpeg":
            commands.append(command)
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", observe)
    main.run(options(tmp_path, media_tools, "-v", media["combined"],
                     "-a", media["english"], "-al", "english", "-a", media["japanese"], "-al", "japanese",
                     "-s", media["en_sub"], "-sl", "english", "-s", media["ja_sub"], "-sl", "japanese",
                     "--language", "english", "--hls-only"))
    root = tmp_path / "cdn/tt1234567"
    assert not list(root.rglob("*.mkv"))
    assert not (root / "english").exists()
    assert not list(root.rglob(".lapis-join-*"))
    expected = {str(media[k].resolve()) for k in ("combined", "english", "japanese", "en_sub", "ja_sub")}
    assert {cmd[cmd.index("-i") + 1] for cmd in commands} == expected
    assert all(cmd.count("-i") == 1 for cmd in commands)
    entry = json.loads((root / "movie.json").read_text())["languages"]["english"]
    assert entry["download"] is None
    assert (tmp_path / "cdn" / entry["master"]).is_file()
    assert_separate(root / "hls/english")
    assert_playlists(root / "hls/english", ["en", "ja"], ["en", "ja"])


def test_hls_only_automatic(tmp_path, media, media_tools, monkeypatch):
    monkeypatch.setattr(main, "copy_original", reject_download)
    main.run(options(tmp_path, media_tools, "--input", media["combined"], "--hls-only"))
    root = tmp_path / "cdn/tt1234567"
    assert not list(root.rglob("*.mkv"))
    assert json.loads((root / "movie.json").read_text())["download"] is None
    assert_separate(root / "hls")
    assert_playlists(root / "hls", ["en", "ja"], ["und"])


def test_hls_only_two_edits_keep_joins_isolated(tmp_path, media, media_tools):
    main.run(options(tmp_path, media_tools, "-v", media["first"], "-v", media["second"],
                     "-a", media["english"], "-al", "en", "-a", media["japanese"], "-al", "ja",
                     "-s", media["en_sub"], "-sl", "en", "-s", media["ja_sub"], "-sl", "ja",
                     "-join", "0:v", "0:a", "0:s", "-jl", "english",
                     "--join", "1:v", "1:a", "1:s", "-jl", "japanese",
                     "--season", "1", "--episode", "2", "--hls-only", kind="show"))
    root = tmp_path / "cdn/tt1234567"
    data = json.loads((root / "show.json").read_text())["seasons"]["1"]["languages"]
    for language, tag, size in (("english", "en", (128, 96)), ("japanese", "ja", (160, 120))):
        hls = root / "s1/hls" / language / "e2"
        master = assert_separate(hls)
        assert master.playlists[0].stream_info.resolution == size
        assert_playlists(hls, [tag], [tag])
        assert data[language]["episodes"]["2"]["download"] is None
    assert not list(root.rglob("*.mkv"))


def test_direct_hls_selected_stream_content(tmp_path, media, media_tools):
    main.run(options(tmp_path, media_tools, "-v", media["two_videos"], "--video-stream", "1",
                     "-a", media["combined"], "--audio-stream", "1", "-al", "ja", "--hls-only"))
    root = tmp_path / "cdn/tt1234567/hls"
    master = assert_separate(root)
    assert master.playlists[0].stream_info.resolution == (160, 120)
    video = subprocess.run(["ffmpeg", "-v", "error", "-i", str(root / master.playlists[0].uri),
                            "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                           check=True, capture_output=True).stdout
    assert sum(video[2::3]) > 10 * max(1, sum(video[0::3]))  # Blue, not first/red video.
    audio = next(t for t in master.media if t.type == "AUDIO")
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(root / audio.uri), "-ac", "1", "-ar", "48000",
                          "-f", "s16le", "-"], check=True, capture_output=True).stdout
    samples = array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    samples = samples[24000:72000]
    crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
    assert 875 <= crossings <= 885  # Second track is 880 Hz; first is 440 Hz.


@pytest.mark.parametrize("offset,expected", [(0, "00:00.250"), (0.5, "00:00.750"), (-0.1, "00:00.150")])
def test_subtitle_timing_and_video_duration(tmp_path, media, media_tools, offset, expected):
    main.run(options(tmp_path, media_tools, "-v", media["first"], "-s", media["en_sub"],
                     "--subtitle-offset", offset, "--hls-only"))
    root = tmp_path / "cdn/tt1234567/hls"
    assert expected + " -->" in (root / "vtt/subtitle-000.vtt").read_text()
    assert m3u8.load(str(root / "vtt/subtitle-000-playlist.m3u8")).segments[0].duration == pytest.approx(2)


def test_direct_audio_offset(tmp_path, media, media_tools):
    main.run(options(tmp_path, media_tools, "-v", media["first"], "-a", media["english"],
                     "--audio-offset", "0.5", "--hls-only"))
    root = tmp_path / "cdn/tt1234567/hls"
    master = assert_separate(root)
    audio = next(t for t in master.media if t.type == "AUDIO")
    audio_start = float(probe(media_segment(root / audio.uri))["streams"][0]["start_time"])
    video_start = float(probe(media_segment(root / master.playlists[0].uri))["streams"][0]["start_time"])
    assert 0.47 <= audio_start - video_start <= 0.51


def test_hls_only_dry_run(tmp_path, media, media_tools, capsys):
    main.run(options(tmp_path, media_tools, "-v", media["first"], "-a", media["combined"],
                     "--audio-stream", "1", "--audio-offset", "0.5", "--hls-only", "--dry-run"))
    data = json.loads(capsys.readouterr().out)
    assert data["hls_only"] is True
    output = data["outputs"][0]
    assert output["mux_command"] is None and output["paths"]["original"] is None
    assert output["hls_sources"][1]["stream_selector"] == "0:a:1"
    assert output["hls_sources"][1]["input_options"][-4:] == ["-itsoffset", "0.5", "-i", str(media["combined"])]
    assert not (tmp_path / "cdn").exists()


def test_hls_only_clears_stale_download_but_keeps_file(tmp_path, media, media_tools):
    root = tmp_path / "cdn/tt1234567"
    root.mkdir(parents=True)
    original = root / "tt1234567.mkv"
    original.write_bytes(b"existing download")
    (root / "movie.json").write_text(json.dumps({"download": "tt1234567/tt1234567.mkv"}))
    main.run(options(tmp_path, media_tools, "-v", media["first"], "--hls-only"))
    assert json.loads((root / "movie.json").read_text())["download"] is None
    assert original.read_bytes() == b"existing download"


def test_shared_container_nonzero_start_keeps_relative_timing(tmp_path, media, media_tools):
    source = tmp_path / "shifted.mkv"
    ffmpeg("-i", media["combined"], "-map", "0", "-c", "copy", "-output_ts_offset", "5", source)
    main.run(options(tmp_path, media_tools, "-v", source, "-a", source, "-s", source, "--hls-only"))
    root = tmp_path / "cdn/tt1234567/hls"
    master = assert_separate(root)
    source_streams = probe(source)["streams"]
    video_start = float(probe(media_segment(root / master.playlists[0].uri))["streams"][0]["start_time"])
    audio = next(t for t in master.media if t.type == "AUDIO")
    audio_start = float(probe(media_segment(root / audio.uri))["streams"][0]["start_time"])
    source_start = float(probe(source)["format"]["start_time"])
    expected_video = 1 + float(source_streams[0]["start_time"]) - source_start
    expected_audio = 1 + float(source_streams[1]["start_time"]) - source_start
    # CFR video rounds to its frame timebase; AAC emits one priming packet.
    assert video_start == pytest.approx(expected_video, abs=0.5 / 24 + 0.001)
    assert audio_start == pytest.approx(expected_audio - 1024 / 48000, abs=0.001)
    assert "00:00.271 -->" in (root / "vtt/subtitle-000.vtt").read_text()


def test_separate_playlists_across_segment_boundaries(tmp_path, media_tools):
    video, audio, subtitle = tmp_path / "video.mkv", tmp_path / "audio.wav", tmp_path / "sub.vtt"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=128x96:r=24:d=23", "-c:v", "libx264",
           "-threads", "1", "-preset", "ultrafast", video)
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=23:sample_rate=48000", audio)
    subtitle.write_text("WEBVTT\n\n00:00:09.500 --> 00:00:10.500\nAcross boundary\n\n"
                        "00:00:21.000 --> 00:00:22.000\nNear end\n")
    main.run(options(tmp_path, media_tools, "-v", video, "-a", audio, "-al", "en",
                     "-s", subtitle, "-sl", "en", "--hls-only"))
    root = tmp_path / "cdn/tt1234567/hls"
    master = assert_separate(root)
    video_playlist = m3u8.load(str(root / master.playlists[0].uri))
    audio_track = next(t for t in master.media if t.type == "AUDIO")
    audio_playlist = m3u8.load(str(root / audio_track.uri))
    assert len(video_playlist.segments) == len(audio_playlist.segments) == 3
    assert sum(s.duration for s in video_playlist.segments) == pytest.approx(23)
    assert sum(s.duration for s in audio_playlist.segments) == pytest.approx(23, abs=0.05)
    for path in (root / master.playlists[0].uri, root / audio_track.uri):
        starts = [float(probe(path.parent / s.uri)["streams"][0]["start_time"])
                  for s in m3u8.load(str(path)).segments]
        assert all(9.9 < b - a < 10.1 for a, b in zip(starts, starts[1:]))
    assert "00:09.500 --> 00:10.500" in (root / "vtt/subtitle-000.vtt").read_text()
    assert m3u8.load(str(root / "vtt/subtitle-000-playlist.m3u8")).segments[0].duration == 23
    assert_playlists(root, ["en"], ["en"])
