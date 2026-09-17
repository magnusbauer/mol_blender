"""Presentation/export of verified Blender frames; never submits render work."""
import hashlib
from pathlib import Path
import threading
from .animation import unpack, export


def remember(cmd, archive):
    path = Path(archive).resolve()
    # A complete archive must be validated even when auto_preview is disabled.
    with unpack(path):
        pass
    cmd._blender_render_movie_archive = (str(path), hashlib.sha256(path.read_bytes()).hexdigest())


def load_movie(cmd, archive):
    if threading.current_thread() is not threading.main_thread():
        return 'fallback_path'
    dispatcher = getattr(cmd, '_call_in_gui_thread', None)
    opengl = getattr(cmd, '_call_with_opengl_context', None)
    if not callable(dispatcher) or dispatcher == opengl:
        return 'fallback_path'
    try:
        # Validate/extract every frame before replacing any existing movie.
        with unpack(archive) as (directory, meta):
            def present():
                saved_view = cmd.get_view()
                state = cmd.get_state()
                cmd.mstop()
                cmd.mset('')
                cmd.mclear()
                cmd.mset(f'{state} x{meta["frames"]}')
                cmd.set('movie_fps', meta['fps'])
                cmd.set('cache_frames', 1)
                cmd.set('single_image', 0)
                cmd.set('ray_trace_frames', 0)
                cmd.set('draw_frames', 0)
                try:
                    for index in range(meta['frames']):
                        cmd.frame(index + 1)
                        result = cmd.load_png(str(directory / f'{index:04d}.png'), movie=1, stereo=0, quiet=1)
                        if result == 0:
                            raise RuntimeError('PyMOL could not cache a movie frame')
                finally:
                    cmd.set_view(saved_view)
                cmd.frame(1)
            dispatcher(present)
        return 'inline_preview'
    except Exception as exc:
        print('Movie files saved; PyMOL movie presentation failed: ' + str(exc))
        return 'fallback_path'


def movie_export(filename, source='', _self=None):
    """Export Blender frames to .mp4 or .gif without rendering again.

    source: saved .frames.zip archive; omitted uses the latest verified animation.
    """
    if _self is None:
        from pymol import cmd as _self
    if str(source).strip():
        archive = Path(str(source)).expanduser().resolve()
    else:
        saved = getattr(_self, '_blender_render_movie_archive', None)
        if not saved:
            raise ValueError('No verified animation; specify source=/path/to/render.frames.zip')
        archive = Path(saved[0])
        if hashlib.sha256(archive.read_bytes()).hexdigest() != saved[1]:
            raise ValueError('Saved animation archive changed; recover the original verified frames')
    destination = Path(str(filename)).expanduser().resolve()
    if destination == archive:
        raise ValueError('Movie export must not overwrite its source archive')
    result = export(archive, destination)
    print('Exported Blender movie: ' + result)
    return result
