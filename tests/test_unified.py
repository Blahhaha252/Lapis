import json
import pytest
from pathlib import Path
from unified import get_parser, prepare, named_tracks

def test_language_required(tmp_path):
    f=tmp_path/"v.mp4";f.write_bytes(b"")
    a=get_parser().parse_args(["--content-type","movie","--imdb","tt12","--rating","PG",
                               "-v",str(f)])
    with pytest.raises(ValueError,match="explicit"):
        prepare(a)

def test_duplicate_languages_without_collision():
    x=[{"kind":"video","language":"en"},{"kind":"video","language":"en"},
       {"kind":"audio","language":"en"}]
    tracks,_=named_tracks([],x,False)
    assert len({t["id"] for t in tracks})==3

def test_replace_scoped_to_kind():
    old=[{"id":"english-01","kind":"video","language":"en"},
         {"id":"english-02","kind":"audio","language":"en"}]
    new=[{"kind":"audio","language":"en"}]
    tracks,retained=named_tracks(old,new,True)
    assert len(retained)==1 and retained[0]["kind"]=="video"
