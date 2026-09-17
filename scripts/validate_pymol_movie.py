"""Run inside a real PyMOL GUI: PYMOL_MOVIE_ARCHIVE and PYMOL_MOVIE_EVIDENCE.

Uses only an already rendered public-fixture archive. Tests native cached-frame
export against the original PNG pixels and invokes the plugin's later exports.
"""
import json
import os
from pathlib import Path
import time
import traceback


def validate():
    from pymol import cmd
    from PIL import Image
    from blender_render.animation import unpack
    from blender_render.pymol_movie import remember, load_movie, movie_export
    root=Path(os.environ['PYMOL_MOVIE_EVIDENCE']);root.mkdir(parents=True,exist_ok=True)
    try:
        archive=Path(os.environ['PYMOL_MOVIE_ARCHIVE'])
        cmd.fragment('ala')
        cmd.mset('1 x2')
        cmd.set('single_image',1)  # Must not collapse the incoming cache to one image.
        saved=cmd.get_view();objects=cmd.get_names('objects')
        remember(cmd,archive)
        assert load_movie(cmd,archive)=='inline_preview'
        with unpack(archive) as (frames,meta):
            assert cmd.count_frames()==meta['frames']
            assert cmd.get_names('objects')==objects
            assert list(cmd.get_view())==list(saved)
            cmd.mplay()
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(450, loop.quit)
            loop.exec()
            playback_frame = cmd.get_frame()
            cmd.mstop()
            assert playback_frame > 1, playback_frame
            cmd.mpng(str(root/'native'),first=1,last=meta['frames'],mode=0,modal=0,
                     width=meta['width'],height=meta['height'])
            exported=sorted(root.glob('native*.png'))
            assert len(exported)==meta['frames'], exported
            for original,actual in zip(sorted(frames.glob('*.png')),exported):
                with Image.open(original) as a,Image.open(actual) as b:
                    assert a.convert('RGB').tobytes()==b.convert('RGB').tobytes(), actual
        assert load_movie(cmd, archive) == 'inline_preview'
        from pymol import movie
        movie.produce(str(root/'native.mp4'), mode=0, encoder='ffmpeg', preserve=1,
                      width=meta['width'], height=meta['height'], _self=cmd)
        # Native produce is asynchronous in GUI builds; let frame export and
        # its encoder finish while Qt continues servicing PyMOL.
        for _ in range(120):
            if not cmd.get_setting_int('keep_alive'):
                break
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(250, loop.quit)
            loop.exec()
        assert not cmd.get_setting_int('keep_alive'), 'Native movie export timed out'
        assert (root/'native.mp4').stat().st_size > 0
        with unpack(archive) as (frames, meta):
            native_frames = sorted((root/'native.tmp').glob('mov*.png'))
            assert len(native_frames) == meta['frames']
            for original, actual in zip(sorted(frames.glob('*.png')), native_frames):
                with Image.open(original) as a, Image.open(actual) as b:
                    assert a.convert('RGB').tobytes() == b.convert('RGB').tobytes(), actual
        movie_export(str(root/'later.mp4'),_self=cmd)
        movie_export(str(root/'later.gif'),_self=cmd)
        (root/'evidence.json').write_text(json.dumps({'pymol':list(cmd.get_version())[:2],
            'frames':meta['frames'],'playback_frame_after_450ms':playback_frame,
            'native_mp4_export':True,'native_cached_png_pixels_equal':True,
            'plugin_exports':['later.mp4','later.gif'],'movie_replaced':True,
            'molecular_view_preserved':True,'objects_preserved':True},indent=2))
        print('PYMOL MOVIE ACCEPTANCE PASSED',flush=True)
    except BaseException:
        (root/'failure.txt').write_text(traceback.format_exc())
        traceback.print_exc()
    finally:
        cmd.quit()


# PyMOL runs startup scripts before its GUI is fully ready; schedule on Qt.
from pymol.Qt import QtCore
from pymol import cmd
print('Scheduling PyMOL movie acceptance', flush=True)
cmd._call_in_gui_thread(lambda: QtCore.QTimer.singleShot(1500, validate))
