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

def test_cover_flag_is_exposed():
    p=get_parser()
    opts=p.parse_args(["--content-type","movie","--imdb","tt123",
        "--rating","PG","--cover-art","poster.jpg","-v","vid.mkv","-vl","en"])
    assert opts.cover_art=="poster.jpg"

def test_optimized_png_with_resize_and_exif(tmp_path,monkeypatch):
    from PIL import Image, ImageOps
    from unified import prepare_cover
    source=tmp_path/"poster.jpg"
    Image.new("RGB",(2000,3000),"orange").save(source)
    output=tmp_path/"new.png"
    recorded={}
    original=Image.Image.save
    def capture(self,fp,*args,**kwargs):
        recorded.update(kwargs)
        return original(self,fp,*args,**kwargs)
    monkeypatch.setattr(Image.Image,"save",capture)
    temp=prepare_cover(source,output)
    with Image.open(temp) as result:
        assert result.size==(720,1080)
        assert result.format=="PNG"
    assert recorded["compress_level"]==9
    assert recorded["optimize"] is True
    temp.unlink()

def test_short_cover_not_upscaled_and_alpha_preserved(tmp_path):
    from PIL import Image
    from unified import prepare_cover
    source=tmp_path/"logo.png"
    Image.new("RGBA",(400,600),(1,2,3,85)).save(source)
    generated=prepare_cover(source,tmp_path/"stored.png")
    with Image.open(generated) as result:
        assert result.size==(400,600)
        assert result.getpixel((0,0))[3]==85
    generated.unlink()

def test_cover_destinations_by_title(tmp_path):
    from types import SimpleNamespace
    from unified import cover_destination
    a=SimpleNamespace(content_type="movie",imdb="tt123")
    assert cover_destination(tmp_path,a,None)==tmp_path/"tt123"/"cover_art.png"
    a.content_type="show"
    assert cover_destination(tmp_path,a,"tt88")==tmp_path/"show_metadata"/"tt88-cover_art.png"
    a.content_type="anime"
    assert cover_destination(tmp_path,a,"tt88")==tmp_path/"anime_metadata"/"tt88-cover_art.png"

def test_200px_rendition_leniency_both_axes(tmp_path,monkeypatch):
    from unified import eligible_rendition
    assert eligible_rendition(1920,900,1080)
    assert eligible_rendition(1720,880,1080)  # Both axes exactly 200px short.
    assert not eligible_rendition(1719,880,1080)  # Width short by 201px.
    assert not eligible_rendition(1720,879,1080)  # Height short by 201px.
    assert not eligible_rendition(1280,720,1080)  # 720p stays 720p.
    commands=[]
    monkeypatch.setattr("unified.command",lambda cmd: commands.append(cmd))
    item={"kind":"video","language":"en","id":"english-01",
          "prefix":"english/video/english-01","path":"dummy.mkv",
          "width":1920,"height":900,"fps":"24/1"}
    results=renditions(item,tmp_path)
    assert results[0]["height"]==1080
    assert results[0]["width"]==2304  # Original 1920:900 aspect ratio retained.
    assert commands[0][commands[0].index("-vf")+1]=="scale=2304:1080"

