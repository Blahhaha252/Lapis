import pytest
from unified import get_parser, prepare, named_tracks, lang, renditions

def args_for(file, language="en"):
    tokens=["--content-type","movie","--imdb","tt1234","--rating","PG","-v",str(file)]
    if language is not None: tokens+=["-vl",language]
    return get_parser().parse_args(tokens)

def test_explicit_language_required(tmp_path):
    f=tmp_path/"v.mp4";f.touch()
    with pytest.raises(ValueError,match="explicit"):
        prepare(args_for(f,None))

def test_multi_stream_container_rejected(tmp_path,monkeypatch):
    f=tmp_path/"v.mkv";f.touch()
    monkeypatch.setattr("unified.probe",lambda path:{"streams":[
        {"codec_type":"video"},{"codec_type":"audio"}]})
    with pytest.raises(ValueError,match="exactly ONE"):
        prepare(args_for(f))

def test_wrong_stream_type_rejected(tmp_path,monkeypatch):
    f=tmp_path/"v.mkv";f.touch()
    monkeypatch.setattr("unified.probe",lambda path:{"streams":[{"codec_type":"audio"}]})
    with pytest.raises(ValueError,match="exactly ONE"):
        prepare(args_for(f))

def test_language_names_and_tags():
    assert lang("English")=="en"
    assert lang("pt-BR")=="pt-BR"
    assert lang("ja")=="ja"

def test_multiple_same_language_allocated_uniquely():
    tracks,_=named_tracks([],[
        {"kind":"video","language":"en"},
        {"kind":"video","language":"en"},
        {"kind":"audio","language":"en"}],False)
    assert len({t["id"] for t in tracks})==3
    assert all("english" in t["id"] for t in tracks)

def test_replace_only_same_kind_and_language():
    old=[{"kind":"video","language":"en","id":"english-01"},
         {"kind":"audio","language":"en","id":"english-02"}]
    updated,kept=named_tracks(old,[{"kind":"audio","language":"en"}],True)
    assert kept==old[:1]
    assert updated[0]["kind"]=="video"
    assert updated[1]["id"] not in {"english-01","english-02"}

def test_add_keeps_duplicate_languages():
    old=[{"kind":"audio","language":"en","id":"english-01"}]
    result,kept=named_tracks(old,[{"kind":"audio","language":"en"}],False)
    assert len(result)==2 and kept==old
    assert result[0]["id"]!=result[1]["id"]

def test_video_ladder_no_original_or_upscale(tmp_path,monkeypatch):
    called=[]
    monkeypatch.setattr("unified.command",lambda cmd:called.append(cmd))
    item={"kind":"video","language":"en","id":"english-01",
          "prefix":"english/video/english-01","path":"dummy.mkv",
          "width":1920,"height":1080,"fps":"30000/1001"}
    result=renditions(item,tmp_path)
    assert [r["height"] for r in result]==[1080,720,480,360,240,144]
    assert all("original" not in r["playlist"] for r in result)
    assert "-crf" in called[0]
    assert "25" in called[0]

def test_show_requires_episode_and_series_imdb():
    p=get_parser()
    a=p.parse_args(["--content-type","show","--imdb","tt1234","--rating","TV-PG",
                    "-v","input.mp4","-vl","en"])
    with pytest.raises(ValueError,match="Series"):
        prepare(a)
