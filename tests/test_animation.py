import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

import pytest

from blender_render import animation
from blender_render.spec import resolve_options


def test_defaults_hash_capabilities_and_still_compatibility():
    still = resolve_options({}, {})
    assert 'animation' not in still['style_doc']['output']
    assert still['preset'] == 'preset_1_interface'
    rock = resolve_options({'animation': 'rock'}, {})
    assert rock['style_doc']['output']['animation'] == dict(mode='rock', duration=3, fps=24, rock_angle=12, gif=False)
    spin = resolve_options({'animation': '360', 'width': 640, 'height': 480}, {})
    assert len(animation.angles(spin['style_doc']['output']['animation'])) == 144
    assert rock['render_spec']['sha256'] != still['render_spec']['sha256']
    assert {'animation.camera_orbit', 'encoding.ffmpeg'} <= set(rock['render_spec']['required_capabilities'])
    rough = resolve_options({'animation': 'rock', 'rough': True}, {})
    assert rough['style_doc']['output']['animation'] == rock['style_doc']['output']['animation']


@pytest.mark.parametrize('options', [
    {'animation':'bad'}, {'animation':'none','gif':False}, {'animation':'rock','fps':True},
    {'animation':'rock','fps':61}, {'animation':'rock','duration':float('nan')},
    {'animation':'rock','duration':16}, {'animation':'rock','duration':.1},
    {'animation':'360','rock_angle':12}, {'animation':'rock','rock_angle':0},
    {'animation':'rock','raw_view':True}, {'animation':'rock','width':8192,'height':8192},
    {'gif':True}, {'animation':'360','duration':15,'fps':60},
])
def test_invalid_before_dispatch(options):
    with pytest.raises(ValueError):
        resolve_options(options, {})


def test_shared_host_rejects_private_paid_can_opt_in():
    with pytest.raises(ValueError, match='hosted animation'):
        resolve_options({'animation':'rock'}, {}, public=True)
    resolved = resolve_options({'animation':'rock'}, {}, public=True, allow_animation=True)
    assert resolved['style_doc']['renderer']['require_gpu']


@pytest.mark.parametrize('preset', [p['id'] for p in __import__('blender_render.presets', fromlist=['catalog']).catalog()])
def test_every_preset_resolves_animation(preset):
    result = resolve_options({'preset':preset,'animation':'360','duration':1,'fps':4,'width':320,'height':240}, {})
    assert result['style_doc']['output']['animation']['mode'] == '360'


@pytest.fixture
def archive(tmp_path):
    from PIL import Image
    frames = tmp_path/'frames'; frames.mkdir()
    for i, color in enumerate(('red','green','blue','yellow')):
        Image.new('RGBA',(32,24),color).save(frames/f'{i:04d}.png')
    path=tmp_path/'movie.frames.zip'
    animation.package_frames(frames,path,dict(mode='360',fps=4,duration=1,gif=True),32,24)
    return path


@pytest.mark.parametrize('attack', ['traversal','duplicate','dimensions','checksum','oversize','symlink'])
def test_archive_rejected_before_movie_mutation(archive, attack):
    with zipfile.ZipFile(archive) as z:
        contents={i.filename:z.read(i) for i in z.infolist()}
    meta=json.loads(contents['frames.json'])
    if attack=='dimensions': meta['width']=2**30
    if attack=='checksum': meta['files'][0]['sha256']='0'*64
    if attack=='oversize': meta['frames']=361
    contents['frames.json']=json.dumps(meta).encode()
    with zipfile.ZipFile(archive,'w') as z:
        for name,raw in contents.items(): z.writestr(name,raw)
        if attack=='traversal': z.writestr('../escape.png',b'x')
        if attack=='duplicate': z.writestr('0000.png',contents['0000.png'])
        if attack=='symlink':
            info=zipfile.ZipInfo('link');info.external_attr=0o120777<<16;z.writestr(info,'/etc/passwd')
    with pytest.raises(ValueError):
        with animation.unpack(archive): pytest.fail('unsafe archive accepted')


def test_unpack_cleanup_and_metadata(archive):
    with animation.unpack(archive) as (directory,meta):
        assert meta['frames']==4
        assert len(list(directory.glob('*.png')))==4
    assert not directory.exists()


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='Needs FFmpeg')
def test_real_export_transparency_and_later_gif(archive,tmp_path):
    from PIL import Image
    animation.export(archive,tmp_path/'out.mp4')
    animation.export(archive,tmp_path/'out.gif')
    for name in ('out.mp4','out.gif'):
        stream=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-show_streams','-of','json',str(tmp_path/name)]))['streams'][0]
        assert int(stream['nb_read_frames'])==4
        assert float(stream['duration'])==pytest.approx(1)
    with Image.open(tmp_path/'out.gif') as im: assert im.info['loop']==0
    frames=tmp_path/'alpha';frames.mkdir()
    for i in range(4): Image.new('RGBA',(31,23),(0,0,0,0)).save(frames/f'{i:04d}.png')
    animation.encode(frames,dict(fps=4,frames=4),tmp_path/'white.gif')
    with Image.open(tmp_path/'white.gif') as im: assert im.convert('RGB').getpixel((0,0))==(255,255,255)
    animation.encode(frames,dict(fps=4,frames=4),tmp_path/'odd.mp4')


@pytest.mark.parametrize('cancel', [True, False])
def test_encoder_interrupts_and_reaps_child(cancel):
    with pytest.raises((TimeoutError,RuntimeError)):
        animation._run([sys.executable,'-c','import time;time.sleep(20)'],
                       deadline=time.monotonic()+.05, cancelled=lambda:cancel)


def test_pymol_import_and_export_without_submission(archive,tmp_path):
    from blender_render.pymol_movie import load_movie,remember,movie_export
    class Cmd:
        def __init__(self): self.calls=[]
        def _call_in_gui_thread(self,fn): return fn()
        def _call_with_opengl_context(self,fn): return fn()
        def get_view(self): return [1]*18
        def get_state(self): return 2
        def __getattr__(self,name):
            return lambda *a,**k:self.calls.append((name,a,k))
    cmd=Cmd();remember(cmd,archive)
    assert load_movie(cmd,archive)=='inline_preview'
    assert ('mset',('2 x4',),{}) in cmd.calls
    assert len([x for x in cmd.calls if x[0]=='load_png'])==4
    assert ('set_view',([1]*18,),{}) in cmd.calls
    if shutil.which('ffmpeg'):
        assert Path(movie_export(tmp_path/'later.gif',_self=cmd)).exists()
    archive.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'): movie_export(tmp_path/'fail.gif',_self=cmd)


def test_animation_worker_capabilities_rejected_before_execution():
    from blender_render.engine import require_compatible, EngineMismatch
    identity = dict(manifest_version=1, protocol_version=1, bundle_versions=['1.0'],
                    schema_versions=['1.0'], engine_build='test', runtime_requirements={},
                    capabilities=['animation.camera_orbit', 'encoding.ffmpeg'])
    old_worker = dict(identity, capabilities=[])
    with pytest.raises(EngineMismatch) as caught:
        require_compatible(identity, old_worker, identity['capabilities'])
    assert caught.value.diagnostic['reason'] == 'capability_missing'
    assert caught.value.diagnostic['before_execution'] is True
