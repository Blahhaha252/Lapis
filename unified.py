"""Strict, explicitly labelled single-stream media to standalone HLS tracks."""
import argparse, collections, fcntl, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path
from fractions import Fraction
import langcodes, yaml

HEIGHTS=(2160,1440,1080,720,480,360,240,144)
CRF={2160:23,1440:23,1080:25,720:25}
RATE={480:(1000000,1250000,2000000),360:(600000,750000,1200000),
      240:(300000,375000,600000),144:(150000,190000,300000)}
RATINGS={"TV-Y":0,"G":1,"TV-Y7":2,"TV-Y7-FV":3,"TV-G":4,"PG":5,
         "TV-PG":6,"PG-13":7,"TV-14":8,"R":9,"TV-MA":10,"NC-17":11}
SUBS={"subrip","ass","ssa","webvtt","mov_text","text"}
LOSSLESS={"flac","alac","wavpack","tta","ape","truehd","mlp"}
ID=re.compile(r"tt[0-9]+$")
def command(cmd):
    subprocess.run([str(c) for c in cmd],check=True)
def probe(path):
    p=subprocess.run(["ffprobe","-v","error","-show_streams","-show_format",
       "-of","json",str(path)],check=True,capture_output=True,text=True)
    return json.loads(p.stdout)
def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".lapis-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(data,f,ensure_ascii=False,indent=2);f.write("\n")
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def read_json(path,default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
def lang(value):
    if not value:raise ValueError("Each input requires explicit -vl, -al or -sl")
    if langcodes.tag_is_valid(value):return langcodes.standardize_tag(value)
    try:tag=str(langcodes.find(value))
    except (LookupError,ValueError) as e:raise ValueError("Invalid language: "+value) from e
    if not langcodes.tag_is_valid(tag):raise ValueError("Invalid language: "+value)
    return tag
def folder(tag):
    name=langcodes.Language.get(tag).display_name("en").lower()
    name=re.sub("[^a-z0-9]+","-",name).strip("-")
    return name or tag.lower().replace("-","_")
class Input(argparse.Action):
    def __call__(self,p,ns,value,option_string=None):
        tracks=getattr(ns,"tracks",None) or []
        kind={"-v":"video","-a":"audio","-s":"subtitle"}[option_string]
        tracks.append(dict(kind=kind,path=value,language=None));ns.tracks=tracks
class Label(argparse.Action):
    def __call__(self,p,ns,value,option_string=None):
        kind={"-vl":"video","-al":"audio","-sl":"subtitle"}[option_string]
        tracks=getattr(ns,"tracks",None) or []
        selected=next((t for t in reversed(tracks) if t["kind"]==kind),None)
        if not selected or selected["language"] is not None:
            p.error(option_string+" needs an unlabelled "+kind+" input")
        selected["language"]=value
def get_parser():
    p=argparse.ArgumentParser(description=__doc__)
    for flag,kind in [("-v","video"),("-a","audio"),("-s","subtitle")]:
        p.add_argument(flag,action=Input,metavar="FILE")
    for flag in ("-vl","-al","-sl"):p.add_argument(flag,action=Label,metavar="LANG")
    p.add_argument("--content-type",choices=("movie","show","anime"),required=True)
    p.add_argument("--imdb",required=True)
    p.add_argument("--show-imdb");p.add_argument("--anime-imdb")
    p.add_argument("--movie-name");p.add_argument("--show-name");p.add_argument("--episode-name")
    p.add_argument("--season",type=int);p.add_argument("--episode",type=int)
    p.add_argument("--rating",required=True);p.add_argument("--year",type=int)
    group=p.add_mutually_exclusive_group()
    group.add_argument("--add",action="store_true")
    group.add_argument("--replace",action="store_true")
    p.add_argument("--output");p.add_argument("--config",default="config.yml")
    p.add_argument("--dry-run",action="store_true")
    return p
def prepare(a):
    if not ID.fullmatch(a.imdb):raise ValueError("Invalid IMDb ID")
    if a.rating.upper() not in RATINGS and a.rating.upper()!="NR":
        raise ValueError("Unsupported content rating")
    if a.content_type=="movie":
        if any((a.show_imdb,a.anime_imdb,a.season,a.episode)):
            raise ValueError("Movie cannot have series or episode flags")
        series=None
    else:
        series=a.show_imdb if a.content_type=="show" else a.anime_imdb
        if not series or not ID.fullmatch(series) or series==a.imdb:
            raise ValueError("Series and episode require distinct IMDb IDs")
        if a.content_type=="show" and a.anime_imdb or a.content_type=="anime" and a.show_imdb:
            raise ValueError("Use only the matching series IMDb flag")
        if not a.season or not a.episode or a.season<1 or a.episode<1:
            raise ValueError("Positive --season and --episode required")
    if not a.tracks:raise ValueError("At least one input is required")
    for t in a.tracks:
        t["language"]=lang(t["language"])
        path=Path(t["path"]).resolve()
        if not path.is_file():raise ValueError("Missing input: "+str(path))
        t["path"]=str(path)
        info=probe(path);streams=info.get("streams",[])
        if len(streams)!=1 or streams[0].get("codec_type")!=t["kind"]:
            raise ValueError(str(path)+" must contain exactly ONE "+t["kind"]+" stream")
        s=streams[0];t["codec"]=s.get("codec_name","")
        t["width"]=s.get("width");t["height"]=s.get("height")
        t["channels"]=s.get("channels")
        t["sample_rate"]=s.get("sample_rate")
        t["fps"]=s.get("avg_frame_rate") or s.get("r_frame_rate") or "25/1"
        t["duration"]=s.get("duration") or info.get("format",{}).get("duration")
        if t["kind"]=="subtitle" and t["codec"] not in SUBS:
            raise ValueError("Subtitle cannot be converted to WebVTT: "+t["codec"])
    return series
def named_tracks(old,new,replace):
    retained=list(old);fresh=[]
    for t in new:
        if replace:
            retained=[x for x in retained if not(x["kind"]==t["kind"] and x["language"]==t["language"])]
        used={x["id"] for x in old+fresh}
        base=folder(t["language"]);num=1
        while base+"-%02d"%num in used:num+=1
        t["id"]=base+"-%02d"%num
        t["prefix"]=base+"/"+t["kind"]+"/"+t["id"]
        fresh.append(t)
    return retained+fresh,retained
def renditions(t,stage):
    width,height=t["width"],t["height"]
    if not width or not height:raise ValueError("Video dimensions unknown")
    try:fps=Fraction(t["fps"])
    except (ZeroDivisionError,ValueError):fps=Fraction(25,1)
    if fps<=0:fps=Fraction(25,1)
    result=[]
    for h in HEIGHTS:
        if h>height:continue
        w=max(2,round(width*h/height/2)*2)
        limited=min(fps,Fraction(25 if h<=240 else 30 if h<=1080 else 60))
        dest=stage/t["prefix"]/(str(h)+"p");dest.mkdir(parents=True,exist_ok=True)
        cmd=["ffmpeg","-nostdin","-v","error","-y","-i",t["path"],"-map","0:0",
            "-an","-sn","-dn","-c:v","libx264","-preset","medium","-pix_fmt","yuv420p",
            "-vf",f"scale={w}:{h}","-r",str(limited)]
        if h in CRF:cmd+=["-crf",str(CRF[h])]
        else:
            b,m,buf=RATE[h];cmd+=["-b:v",str(b),"-maxrate",str(m),"-bufsize",str(buf)]
        gop=max(1,round(limited*10))
        cmd+=["-g",str(gop),"-keyint_min",str(gop),"-sc_threshold","0",
              "-force_key_frames","expr:gte(t,n_forced*10)","-f","hls","-hls_time","10",
              "-hls_playlist_type","vod","-hls_flags","independent_segments",
              "-hls_segment_type","mpegts","-hls_segment_filename",str(dest/"segment-%05d.ts"),
              str(dest/"playlist.m3u8")]
        command(cmd)
        result.append(dict(height=h,width=w,playlist=(dest/"playlist.m3u8").relative_to(stage).as_posix()))
    if not result:raise ValueError("Input video is smaller than 144p")
    return result
def audio(t,stage):
    dest=stage/t["prefix"];dest.mkdir(parents=True,exist_ok=True)
    lossless=t["codec"] in LOSSLESS or t["codec"].startswith("pcm_")
    c=["ffmpeg","-nostdin","-v","error","-y","-i",t["path"],"-map","0:0","-vn","-sn","-dn"]
    if lossless:c+=["-c:a","flac","-strict","-2","-hls_segment_type","fmp4"]
    else:c+=["-c:a","copy","-hls_segment_type","mpegts" if t["codec"] in ("aac","mp3","ac3") else "fmp4"]
    ext="m4s" if lossless or t["codec"] not in ("aac","mp3","ac3") else "ts"
    c+=["-f","hls","-hls_time","10","-hls_playlist_type","vod",
         "-hls_segment_filename",str(dest/("segment-%05d."+ext)),str(dest/"playlist.m3u8")]
    command(c)
    return (dest/"playlist.m3u8").relative_to(stage).as_posix()
def subtitles(t,stage,video_duration=None):
    dest=stage/"subtitles";dest.mkdir(parents=True,exist_ok=True)
    vtt=dest/(t["id"]+".vtt")
    command(["ffmpeg","-nostdin","-v","error","-y","-i",t["path"],
             "-map","0:0","-f","webvtt",str(vtt)])
    duration=max(1,float(video_duration or t["duration"] or 1));pl=dest/(t["id"]+".m3u8")
    pl.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:%d\n"
                  "#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD\n"
                  "#EXTINF:%.3f,\n%s\n#EXT-X-ENDLIST\n"%(int(duration+1),duration,vtt.name))
    return pl.relative_to(stage).as_posix()
def remux(master,retained,new,alltracks):
    if retained and not master.exists():raise ValueError("Existing master.mkv missing")
    old_streams=probe(master).get("streams",[]) if retained else []
    for t in retained:
        if "download_stream" not in t or t["download_stream"]>=len(old_streams):
            raise ValueError("Invalid old download mapping")
    temp=master.with_name(".lapis-master.tmp.mkv")
    try:
        cmd=["ffmpeg","-nostdin","-v","error","-y"]
        if retained:cmd+=["-i",str(master)]
        for t in new:cmd+=["-i",t["path"]]
        for t in retained:cmd+=["-map","0:"+str(t["download_stream"])]
        for i in range(len(new)):cmd+=["-map",str(i+bool(retained))+":0"]
        cmd+=["-map_metadata","-1","-c","copy"]
        for i,t in enumerate(alltracks):
            # BCP47 track tags are kept in tracks.json; Matroska supports tag metadata.
            cmd+=["-metadata:s:"+str(i),"language="+t["language"]]
        cmd.append(str(temp));command(cmd)
        for i,t in enumerate(alltracks):t["download_stream"]=i
        os.replace(temp,master)
    finally:
        if temp.exists():temp.unlink()
def manifest(root,tracks):
    lines=["#EXTM3U","#EXT-X-VERSION:7"]
    audio_tracks=[t for t in tracks if t["kind"]=="audio"]
    subtitles_tracks=[t for t in tracks if t["kind"]=="subtitle"]
    for i,t in enumerate(audio_tracks):
        lines.append('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="%s",LANGUAGE="%s",DEFAULT=%s,AUTOSELECT=YES,URI="%s"'%
                     (t["id"],t["language"],"YES" if i==0 else "NO",t["playlist"]))
    for t in subtitles_tracks:
        lines.append('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subtitles",NAME="%s",LANGUAGE="%s",DEFAULT=NO,AUTOSELECT=YES,URI="%s"'%
                     (t["id"],t["language"],t["playlist"]))
    for t in tracks:
        if t["kind"]!="video":continue
        for r in t["renditions"]:
            # Keep source identity; the player must retain it during ABR selection.
            attrs=["BANDWIDTH="+str({2160:14000000,1440:8000000,1080:5000000,720:3000000,
               480:1400000,360:900000,240:500000,144:300000}[r["height"]]),
               "RESOLUTION=%dx%d"%(r["width"],r["height"]), 'VIDEO-ID="%s"'%t["id"]]
            if audio_tracks:attrs.append('AUDIO="audio"')
            if subtitles_tracks:attrs.append('SUBTITLES="subtitles"')
            lines+=["#EXT-X-STREAM-INF:"+",".join(attrs),r["playlist"]]
    (root/"master.m3u8").write_text("\n".join(lines)+"\n")
def catalogs(root,a,series):
    base=root/a.imdb
    entry={"type":a.content_type,"imdb":a.imdb,
           "name":a.movie_name if not series else a.episode_name,
           "rating":a.rating.upper(),"master":"hls/master.m3u8","download":"master.mkv"}
    if a.year:entry["year"]=a.year
    if series:entry.update(series_imdb=series,season=a.season,episode=a.episode)
    metadata=base/("episode.json" if series else "movie.json")
    write_json(metadata,entry)
    if series:
        path=root/(a.content_type+"_metadata")/(series+".json")
        data=read_json(path,{"type":a.content_type,"imdb":series,
                              "name":a.show_name,"seasons":{}})
        if data["type"]!=a.content_type:raise ValueError("Series type collision")
        if a.show_name:data["name"]=a.show_name
        entries=data.setdefault("seasons",{}).setdefault(str(a.season),{"episodes":{}})["episodes"]
        for number,other in entries.items():
            if number!=str(a.episode) and other.get("imdb")==a.imdb:
                raise ValueError("Episode IMDb assigned twice in this season")
        entries[str(a.episode)]={"imdb":a.imdb,"name":a.episode_name,
          "rating":a.rating.upper(),"master":a.imdb+"/hls/master.m3u8",
          "download":a.imdb+"/master.mkv"}
        values=[e["rating"] for s in data["seasons"].values()
                for e in s.get("episodes",{}).values()]
        data["rating"]=max(values,key=lambda x:RATINGS.get(x,-1))
        write_json(path,data)
        content_id=series;rating=data["rating"]
    else:
        path=metadata;content_id=a.imdb;rating=a.rating.upper()
    globalpath=root/"global.json"
    g=read_json(globalpath,{"version":2,"contents":[]})
    if not isinstance(g.get("contents"),list):
        raise ValueError("Legacy global.json incompatible with v2")
    g["version"]=2
    g["contents"]=[e for e in g["contents"] if e.get("imdb")!=content_id]
    g["contents"].append({"imdb":content_id,"type":a.content_type,
        "rating":rating,"metadata":path.relative_to(root).as_posix()})
    write_json(globalpath,g)
def move_tree(source,dest):
    # Merge individual track directories without deleting sibling tracks.
    for child in source.iterdir():
        target=dest/child.name
        if child.is_dir():
            target.mkdir(parents=True,exist_ok=True)
            move_tree(child,target)
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            os.replace(child,target)
def publish(a):
    series=prepare(a)
    root=Path(a.output).resolve() if a.output else Path(yaml.safe_load(
        Path(a.config).read_text(encoding="utf-8"))["output"]).resolve()
    if a.dry_run:
        print(json.dumps({"root":str(root),"series":series,"inputs":a.tracks},indent=2))
        return
    root.mkdir(parents=True,exist_ok=True)
    with (root/".lapis.lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        base=root/a.imdb;hls=base/"hls"
        old=read_json(hls/"tracks.json",{"tracks":[]})["tracks"]
        if old and not(a.add or a.replace):
            raise ValueError("IMDb exists: choose --add or --replace")
        if not old and (base/"master.mkv").exists():
            raise ValueError("Old structure detected; refusing unsafe migration")
        tracks,retained=named_tracks(old,a.tracks,a.replace)
        if not any(t["kind"]=="video" for t in tracks):
            raise ValueError("At least one video track is required")
        base.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix=".lapis-stage-",dir=base))
        video_duration=max([float(t.get("duration") or 0) for t in a.tracks if t["kind"]=="video"]+[float(t.get("duration") or 0) for t in old if t["kind"]=="video"]+[1])
        try:
            for t in a.tracks:
                if t["kind"]=="video":t["renditions"]=renditions(t,stage)
                elif t["kind"]=="audio":t["playlist"]=audio(t,stage)
                else:t["playlist"]=subtitles(t,stage,video_duration)
            # Remux preserved original tracks and newly provided streams without transcoding.
            remux(base/"master.mkv",retained,a.tracks,tracks)
            # Publish staged media paths only after all encodes and download succeed.
            hls.mkdir(exist_ok=True)
            move_tree(stage,hls)
            # Avoid leaving deleted tracks on the CDN when replacement was requested.
            oldids={t["id"] for t in retained}
            for t in old:
                if t["id"] not in oldids:
                    obsolete=hls/t["prefix"]
                    if obsolete.is_dir():shutil.rmtree(obsolete)
                    if t["kind"]=="subtitle":
                        for suffix in (".vtt",".m3u8"):
                            (hls/"subtitles"/(t["id"]+suffix)).unlink(missing_ok=True)
            serializable=[{k:v for k,v in t.items() if k!="path"} for t in tracks]
            manifest(hls,serializable)
            write_json(hls/"tracks.json",{"version":2,"master":"master.m3u8",
                         "tracks":serializable})
            catalogs(root,a,series)
        finally:shutil.rmtree(stage,ignore_errors=True)
def cli(argv=None):
    try:publish(get_parser().parse_args(argv));return 0
    except (ValueError,KeyError,OSError,subprocess.CalledProcessError) as e:
        print("Lapis error:",e,file=sys.stderr);return 1
