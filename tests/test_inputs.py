import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest
import main
from inputs import build_plans, explicit_language, mux_command, mux_original
from reader import Reader


def parse(tmp_path, *options, kind='movie'):
    return main.create_parser().parse_args(['--content-type', kind, '--imdb', 'tt1234567', '--rating', 'PG',
                                           '--output', str(tmp_path / 'output'), *map(str, options)])

@pytest.fixture
def files(tmp_path):
    result = []
    for name in ('v0.mkv', 'v1.mkv', 'a0.m4a', 'a1.acc', 's0.vtt', 's1.vtt'):
        path = tmp_path / name
        path.touch()
        result.append(path)
    return result

def plans(args):
    return build_plans(args, main.normalize_language)

@pytest.mark.parametrize('value,expected', [('english','en'), ('spanish','es'), ('japanese','ja'),
    ('日本語','ja'), ('eng','en'), ('pt_BR','pt-BR'), ('zh-Hant','zh-Hant'),
    ('yue','yue'), ('Klingon','tlh'), ('x-custom','x-custom'), ('und','und'), ('zxx','zxx')])
def test_languages(value, expected):
    assert explicit_language(value) == expected

@pytest.mark.parametrize('value', ['', '../../english', 'invalid/language', 'gibberishxxxx'])
def test_bad_language(value):
    with pytest.raises(ValueError):
        explicit_language(value)

def test_independent_indices_and_labels(tmp_path, files):
    v0,v1,a0,a1,s0,s1 = files
    args = parse(tmp_path, '-v',v0, '-a',a0, '-al','english', '-v',v1, '-s',s0, '-sl','english',
                 '-a',a1, '-al','spanish', '-s',s1, '-sl','spanish',
                 '-join','0:v','0:a', '-jl','english', '--join','1:v','1:a', '-jl','spanish')
    en,es = plans(args)
    assert en.video.path == str(v0) and es.video.path == str(v1)
    assert en.audio[0].path == str(a0) and es.audio[0].path == str(a1)
    assert en.audio[0].language == 'en' and es.audio[0].language == 'es'
    assert [s.language for s in en.subtitles] == ['en','es']
    assert (en.language,es.language) == ('english','spanish')

def test_single_video_default(tmp_path, files):
    plan, = plans(parse(tmp_path, '-v',files[0], '-a',files[2], '-al','english',
                        '-a',files[3], '-al','spanish', '-s',files[4], '-sl','english'))
    assert len(plan.audio) == 2 and len(plan.subtitles) == 1
    assert plan.language is None

def test_subtitle_subset_and_reused_video(tmp_path, files):
    first,second = plans(parse(tmp_path, '-v',files[0], '-a',files[2], '-a',files[3], '-s',files[4], '-s',files[5],
                               '-join','0:v','0:a','0:s','-jl','en', '-join','0:v','1:a','1:s','-jl','ja'))
    assert first.video == second.video
    assert [s.index for s in first.subtitles] == [0]
    assert [s.index for s in second.subtitles] == [1]

@pytest.mark.parametrize('options', [['-al','english'], ['-sl','japanese'], ['-jl','english'],
    ['--audio-stream','1'], ['--subtitle-offset','0.5'], ['-v','file','--video-stream','-1'],
    ['-a','file','--audio-offset','nan'], ['-a','file','--audio-offset','inf']])
def test_invalid_modifiers(tmp_path, options):
    with pytest.raises(SystemExit):
        parse(tmp_path, *options)

@pytest.mark.parametrize('references', [['2:v'], ['0:q'], ['v:0'], ['0:a'], ['0:v','0:v'],
                                        ['0:v','1:v'], ['0:v','0:a','0:a']])
def test_invalid_joins(tmp_path, files, references):
    with pytest.raises(ValueError):
        plans(parse(tmp_path, '-v',files[0], '-v',files[1], '-a',files[2], '-join',*references))

@pytest.mark.parametrize('labels', [[], ['-jl','english']])
def test_multiple_joins_need_distinct_labels(tmp_path, files, labels):
    with pytest.raises(ValueError):
        plans(parse(tmp_path, '-v',files[0], '-join','0:v',*labels, '-join','0:v',*labels))

def test_multiple_videos_need_joins(tmp_path, files):
    with pytest.raises(ValueError, match='Multiple -v'):
        plans(parse(tmp_path, '-v',files[0], '-v',files[1]))

def test_cannot_mix_modes(tmp_path, files):
    with pytest.raises(ValueError, match='either --input'):
        plans(parse(tmp_path, '--input',files[0], '-v',files[1]))

def test_video_required(tmp_path, files):
    with pytest.raises(ValueError, match='requires at least one'):
        plans(parse(tmp_path, '-a',files[2]))

def test_missing_file(tmp_path):
    with pytest.raises(ValueError, match='not a file'):
        plans(parse(tmp_path, '-v',tmp_path/'absent.mkv'))

def test_path_traversal_label(tmp_path, files):
    with pytest.raises(ValueError, match='Language must'):
        plans(parse(tmp_path, '-v',files[0], '-join','0:v','-jl','../oops'))

@pytest.mark.parametrize('kind,season,prefix', [('movie',None,''), ('ova',None,''), ('show',1,'s1/'), ('anime',None,'')])
def test_language_layouts(tmp_path, kind, season, prefix):
    opts = ['--language','english']
    if kind in ('show','anime'):
        opts += ['--episode','3']
    if season is not None:
        opts += ['--season',str(season)]
    args = parse(tmp_path, *opts, kind=kind)
    paths = main.build_output_paths('joined.mkv', args, create=False)
    basename = 'e3' if kind in ('show','anime') else args.imdb
    suffix = f'/{basename}' if kind in ('show','anime') else ''
    assert paths['original'].relative_to(paths['root']).as_posix() == f'{prefix}english/{basename}.mkv'
    assert paths['master'].relative_to(paths['root']).as_posix() == f'{prefix}hls/english{suffix}/master.m3u8'
    assert not paths['root'].exists()

@pytest.mark.parametrize('kind,season', [('movie',None), ('ova',None), ('anime',1), ('show',None)])
def test_metadata_keeps_variants(tmp_path, kind, season):
    for language in ('english','japanese'):
        opts = ['--language',language,'--episode','2','--episode-name','Episode title']
        if season is not None:
            opts += ['--season',str(season)]
        args = parse(tmp_path, *opts, kind=kind)
        paths = main.build_output_paths('joined.mkv',args)
        main.write_content_json(paths,args)
    data = json.loads((paths['root']/f'{kind}.json').read_text())
    container = data['seasons']['1'] if season is not None else data
    assert set(container['languages']) == {'english','japanese'}
    for language,variant in container['languages'].items():
        item = variant['episodes']['2'] if kind in ('show','anime') else variant
        assert f'/{language}/' in item['master'] and f'/{language}/' in item['download']

@pytest.mark.parametrize('explicit', [None, '/some/path/mediainfo', 'custom-mediainfo'])
def test_mediainfo_resolution(monkeypatch, explicit):
    seen = []
    def run(command, **kwargs):
        seen.append(command)
        return SimpleNamespace(stdout='{"media":{"track":[]}}')
    monkeypatch.setattr(subprocess,'run',run)
    Reader('video.mkv',True,True,explicit).probe_media()
    bundled = Path(main.__file__).parent/'mi/mediainfo'
    assert seen[0][0] == (explicit or (str(bundled) if bundled.is_file() else 'mediainfo'))

def test_mux_failure_preserves_download(tmp_path, files, monkeypatch):
    plan, = plans(parse(tmp_path,'-v',files[0]))
    reader = SimpleNamespace(audio_tracks=[],subtitle_tracks=[])
    output = tmp_path/'previous.mkv'
    output.write_bytes(b'old download')
    def fail(command, **kwargs):
        Path(command[-1]).write_bytes(b'partial output')
        raise subprocess.CalledProcessError(1,command)
    monkeypatch.setattr(subprocess,'run',fail)
    with pytest.raises(subprocess.CalledProcessError):
        mux_original(plan,reader,output)
    assert output.read_bytes() == b'old download'
    assert not list(tmp_path.glob('.lapis-join-*'))

def test_stream_and_offset_binding(tmp_path, files):
    plan, = plans(parse(tmp_path,'-v',files[0],'--video-stream','1','-a',files[2],'--audio-stream','2',
                        '--audio-offset','0.5','-a',files[3],'-s',files[4],'--subtitle-offset','-0.25'))
    assert plan.video.stream == 1
    assert (plan.audio[0].stream,plan.audio[0].offset) == (2,0.5)
    assert (plan.audio[1].stream,plan.audio[1].offset) == (0,0)
    assert plan.subtitles[0].offset == -0.25

def test_mux_maps_shared_file_once(tmp_path, files):
    plan, = plans(parse(tmp_path,'-v',files[0],'-a',files[0],'--audio-stream','1'))
    reader = SimpleNamespace(audio_tracks=[{'language':'ja'}],subtitle_tracks=[])
    command = mux_command(plan,reader,tmp_path/'joined.mkv')
    assert command.count('-i') == 1
    assert [command[i+1] for i,v in enumerate(command) if v=='-map'] == ['0:v:0','0:a:1']

def test_legacy_movie_metadata_migration(tmp_path):
    args = parse(tmp_path,'--language','japanese','--year','2026')
    paths = main.build_output_paths('joined.mkv',args)
    (paths['root']/'movie-kids.json').write_text(json.dumps({'name':'Existing title','rating':'PG','custom':'keep me'}))
    main.write_content_json(paths,args)
    data = json.loads((paths['root']/'movie.json').read_text())
    assert data['name']=='Existing title' and data['custom']=='keep me'
    assert data['languages']['japanese']['year']=='2026'
    assert (paths['root']/'movie-kids.json').exists()

def test_other_seasons_survive(tmp_path):
    args = parse(tmp_path,'--season','2','--episode','3','--language','english',kind='show')
    paths = main.build_output_paths('joined.mkv',args)
    (paths['root']/'show.json').write_text(json.dumps({'seasons':{'1':{'episodes':{'1':'Old name'}},
        '2':{'languages':{'japanese':{'episodes':{'3':'Other dub'}}}}}}))
    main.write_content_json(paths,args)
    data=json.loads((paths['root']/'show.json').read_text())
    assert data['seasons']['1']['episodes']=={'1':'Old name'}
    assert data['seasons']['2']['languages']['japanese']['episodes']=={'3':'Other dub'}
    assert data['seasons']['2']['languages']['english']['episodes']['3']['name']=='Episode 3'
