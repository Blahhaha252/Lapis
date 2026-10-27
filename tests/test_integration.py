import json
from pathlib import Path
import subprocess
import sys
import m3u8
import pytest
import main
from inputs import build_plans, inspect_plans, mux_original


def options(tmp_path, media_tools, *selection, kind='movie'):
    return main.create_parser().parse_args(['--content-type',kind,'--imdb','tt1234567','--rating','PG',
        '--output',str(tmp_path/'cdn'),'--mediainfo-path',media_tools,*map(str,selection)])

def probe(path):
    r=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],
                     check=True,capture_output=True,text=True)
    return json.loads(r.stdout)

def assert_playlists(root, audio_languages, subtitle_languages):
    master=m3u8.load(str(root/'master.m3u8'))
    assert [t.language for t in master.media if t.type=='AUDIO']==audio_languages
    assert [t.language for t in master.media if t.type=='SUBTITLES']==subtitle_languages
    assert master.playlists
    for item in [*master.media,*master.playlists]:
        path=root/item.uri
        assert path.is_file()
        playlist=m3u8.load(str(path))
        assert playlist.segments
        for segment in playlist.segments:
            assert (path.parent/segment.uri).is_file()
    subprocess.run(['ffmpeg','-v','error','-i',str(root/'master.m3u8'),'-map','0:v:0','-map','0:a:0?',
                    '-f','null','-'],check=True,capture_output=True)

def test_manual_multi_audio_subtitles(tmp_path,media,media_tools):
    args=options(tmp_path,media_tools,'-v',media['combined'],'-a',media['english'],'-al','english',
        '-a',media['japanese'],'-al','japanese','-s',media['en_sub'],'-sl','english','-s',media['ja_sub'],'-sl','japanese')
    main.run(args)
    root=tmp_path/'cdn/tt1234567'
    streams=probe(root/'tt1234567.mkv')['streams']
    assert [s['codec_type'] for s in streams]==['video','audio','audio','subtitle','subtitle']
    assert [s['tags'].get('language') for s in streams[1:]]==['en','ja','en','ja']
    assert_playlists(root/'hls',['en','ja'],['en','ja'])
    assert 'こんにちは' in (root/'hls/vtt/subtitle-001.vtt').read_text()
    tracks=json.loads((root/'hls/tracks.json').read_text())
    assert [t['source_file'] for t in tracks['audio']]==['english.m4a','japanese.acc']
    assert not list(root.rglob('.lapis-join-*'))

def test_two_joined_versions(tmp_path,media,media_tools):
    args=options(tmp_path,media_tools,'-v',media['first'],'-v',media['second'],
        '-a',media['english'],'-al','english','-a',media['japanese'],'-al','japanese',
        '-s',media['en_sub'],'-sl','english','-s',media['ja_sub'],'-sl','japanese',
        '-join','0:v','0:a','0:s','-jl','english','--join','1:v','1:a','1:s','-jl','japanese',
        '--season','1','--episode','4',kind='anime')
    main.run(args)
    root=tmp_path/'cdn/tt1234567'
    data=json.loads((root/'anime.json').read_text())
    assert set(data['seasons']['1']['languages'])=={'english','japanese'}
    for language,tag,video,audio in [('english','en',media['first'],media['english']),
                                    ('japanese','ja',media['second'],media['japanese'])]:
        original=root/'s1'/language/'e4.mkv'
        streams=probe(original)['streams']
        assert [s['codec_type'] for s in streams]==['video','audio','subtitle']
        assert streams[1]['tags']['language']==tag
        def packet_hash(path):
            return subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:v:0','-c','copy',
                '-f','hash','-hash','sha256','-'],capture_output=True,check=True).stdout
        assert packet_hash(original)==packet_hash(video)
        def pcm(path):
            return subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-f','s16le','-'],
                                  capture_output=True,check=True).stdout
        actual,expected=pcm(original),pcm(audio)
        # AAC priming may be represented differently across containers.
        assert len(actual)>=len(expected)-4096
        assert expected[8192:16384] in actual
        assert_playlists(root/'s1/hls'/language/'e4',[tag],[tag])
        entry=data['seasons']['1']['languages'][language]['episodes']['4']
        assert (tmp_path/'cdn'/entry['master']).exists()
        assert (tmp_path/'cdn'/entry['download'])==original

def test_selected_audio_stream(tmp_path,media,media_tools):
    args=options(tmp_path,media_tools,'-v',media['combined'],'-a',media['combined'],'--audio-stream','1',
                 '-al','japanese','--language','japanese')
    main.run(args)
    root=tmp_path/'cdn/tt1234567'
    streams=probe(root/'japanese/tt1234567.mkv')['streams']
    assert [s['codec_type'] for s in streams]==['video','audio']
    assert streams[1]['tags']['language']=='ja'
    assert_playlists(root/'hls/japanese',['ja'],[])

def test_legacy_automatic_mode(tmp_path,media,media_tools):
    main.run(options(tmp_path,media_tools,'--input',media['combined']))
    root=tmp_path/'cdn/tt1234567'
    assert (root/'tt1234567.mkv').read_bytes()==media['combined'].read_bytes()
    assert_playlists(root/'hls',['en','ja'],['und'])

def test_video_only_excludes_embedded_tracks(tmp_path,media,media_tools):
    main.run(options(tmp_path,media_tools,'-v',media['combined']))
    root=tmp_path/'cdn/tt1234567'
    assert [s['codec_type'] for s in probe(root/'tt1234567.mkv')['streams']]==['video']
    assert_playlists(root/'hls',[],[])

def test_dry_run_json_no_writes(tmp_path,media,media_tools):
    args=['--content-type','movie','--imdb','tt1234567','--rating','PG','--output',str(tmp_path/'cdn'),
          '--mediainfo-path',media_tools,'-v',str(media['first']),'-a',str(media['english']),
          '-al','english','--width','128','--dry-run']
    result=subprocess.run([sys.executable,str(Path(main.__file__)),*args],check=True,capture_output=True,text=True)
    assert json.loads(result.stdout)['outputs'][0]['audio'][0]['language']=='en'
    assert not (tmp_path/'cdn').exists()

def test_preflight_all_joins(tmp_path,media,media_tools):
    args=options(tmp_path,media_tools,'-v',media['first'],'-a',media['english'],'--audio-stream','99',
                 '-join','0:v','-jl','en','-join','0:v','0:a','-jl','ja')
    with pytest.raises(ValueError,match='no Audio stream 99'):
        main.run(args)
    assert not (tmp_path/'cdn').exists()

def test_positive_audio_offset(tmp_path,media,media_tools):
    args=options(tmp_path,media_tools,'-v',media['first'],'-a',media['english'],'--audio-offset','0.5')
    plan,=build_plans(args,main.normalize_language)
    reader,=inspect_plans([plan],args)
    output=tmp_path/'offset.mkv'
    mux_original(plan,reader,output)
    streams=probe(output)['streams']
    assert 0.47<=float(streams[1]['start_time'])-float(streams[0]['start_time'])<=0.51

def test_second_video_dimensions_and_rate(tmp_path,media,media_tools):
    main.run(options(tmp_path,media_tools,'-v',media['two_videos'],'--video-stream','1'))
    master=m3u8.load(str(tmp_path/'cdn/tt1234567/hls/master.m3u8'))
    assert master.playlists[0].stream_info.resolution==(160,120)
    assert master.playlists[0].stream_info.frame_rate==25

def test_qualities_fractional_rate_default_audio(tmp_path,media,media_tools):
    args=options(tmp_path,media_tools,'-v',media['larger'],'-a',media['english'],'-al','en',
                 '-a',media['japanese'],'-al','ja','--default-audio','1')
    main.run(args)
    root=tmp_path/'cdn/tt1234567'
    master=m3u8.load(str(root/'hls/master.m3u8'))
    assert [p.stream_info.resolution[1] for p in master.playlists]==[144,240,288]
    assert all(p.stream_info.frame_rate==23.976 for p in master.playlists)
    for group in {p.stream_info.audio for p in master.playlists}:
        tracks=[t for t in master.media if t.type=='AUDIO' and t.group_id==group]
        assert [t.language for t in tracks if t.default=='YES']==['ja']
    assert probe(root/'tt1234567.mkv')['streams'][2]['disposition']['default']==1

def test_no_video_in_audio_file(tmp_path,media,media_tools):
    with pytest.raises(ValueError,match='no Video stream 0'):
        main.run(options(tmp_path,media_tools,'-v',media['english']))
    assert not (tmp_path/'cdn').exists()
