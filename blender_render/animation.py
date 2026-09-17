"""Shared animation validation, lossless frame transport and CPU-only export."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import zipfile

CONTROLS = ('animation', 'duration', 'fps', 'rock_angle', 'gif')
MAX_RGBA = 1024**3
MAX_ARCHIVE = 512 * 1024**2
MAX_FRAMES = 360


def normalize(value, output):
    if not isinstance(value, dict) or set(value) - {'mode', 'duration', 'fps', 'rock_angle', 'gif'}:
        raise ValueError('Invalid animation fields')
    mode = value.get('mode', 'none')
    if mode not in ('none', 'rock', '360'):
        raise ValueError('animation must be none, rock or 360')
    if mode == 'none':
        if set(value) - {'mode'}:
            raise ValueError('Animation controls require rock or 360')
        return None
    if output.get('raw_view'):
        raise ValueError('Animation is incompatible with raw_view')
    fps = value.get('fps', 24)
    duration = value.get('duration', 3 if mode == 'rock' else 6)
    if type(fps) is not int or not 1 <= fps <= 60:
        raise ValueError('Animation fps must be an integer from 1 to 60')
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 15:
        raise ValueError('Animation duration must be finite, positive and at most 15 seconds')
    frames = round(fps * duration)
    if not math.isclose(frames, fps * duration, abs_tol=1e-8) or not 4 <= frames <= MAX_FRAMES:
        raise ValueError('Animation must contain an integral number of frames from 4 to 360')
    if frames * output['width'] * output['height'] * 4 > MAX_RGBA:
        raise ValueError('Animation exceeds 1 GiB of decoded RGBA frames; reduce resolution or duration')
    gif = value.get('gif', False)
    if type(gif) is not bool:
        raise ValueError('gif must be a boolean')
    result = dict(mode=mode, duration=duration, fps=fps, gif=gif)
    if mode == 'rock':
        angle = value.get('rock_angle', 12)
        if type(angle) not in (int, float) or not math.isfinite(angle) or not 0 < angle <= 45:
            raise ValueError('rock_angle must be in (0, 45] degrees')
        result['rock_angle'] = angle
    elif 'rock_angle' in value:
        raise ValueError('rock_angle applies only to rock')
    return result


def resolve(options, output):
    value = dict(output.get('animation', {}))
    for key in CONTROLS:
        if key in options:
            value['mode' if key == 'animation' else key] = options[key]
    result = normalize(value, output)
    if result is None:
        output.pop('animation', None)
    else:
        output['animation'] = result
    return result


def angles(config):
    count = round(config['duration'] * config['fps'])
    if config['mode'] == '360':
        return [360 * i / count for i in range(count)]
    return [config['rock_angle'] * math.sin(2 * math.pi * i / count) for i in range(count)]


def required_names(style):
    output = style.get('output', {})
    names = ['render.png']
    if output.get('save_blend'):
        names.append('render.blend')
    cfg = output.get('animation')
    if cfg and cfg.get('mode') in ('rock', '360'):
        names += ['render.mp4', 'frames.zip']
        if cfg.get('gif'):
            names.append('render.gif')
    return names


def encoder():
    executable = shutil.which('ffmpeg')
    if not executable:
        raise ValueError('Animation export requires FFmpeg with libx264 on the execution host')
    result = subprocess.run([executable, '-hide_banner', '-encoders'], capture_output=True, text=True,
                            timeout=10, check=True)
    if 'libx264' not in result.stdout or ' gif ' not in result.stdout:
        raise ValueError('FFmpeg requires libx264 and GIF encoders')
    return executable


def _run(command, *, deadline=None, cancelled=None):
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            while process.poll() is None:
                if cancelled and cancelled():
                    raise RuntimeError('Animation encoding cancelled')
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError('Animation execution deadline expired')
                time.sleep(.05)
            if process.returncode:
                log.seek(0)
                raise RuntimeError('FFmpeg failed: ' + log.read(4096).decode(errors='replace'))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def encode(frames, metadata, destination, *, executable=None, deadline=None, cancelled=None):
    destination = Path(destination)
    if destination.suffix.lower() not in ('.mp4', '.gif'):
        raise ValueError('Movie export filename must end in .mp4 or .gif')
    executable = executable or encoder()
    # Composite unpremultiplied PNG alpha onto white. Pad odd MP4 dimensions,
    # preserving every rendered pixel rather than cropping/reframing the image.
    white = 'split[fg][bg];[bg]lutrgb=r=255:g=255:b=255,lut=a=255[white];[white][fg]overlay=shortest=1:format=auto'
    filters = (white + ',pad=ceil(iw/2)*2:ceil(ih/2)*2:color=white[v]' if destination.suffix.lower() == '.mp4'
               else white + ',split[a][b];[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=sierra2_4a[v]')
    codec = ['-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart'] if destination.suffix.lower() == '.mp4' else ['-loop', '0']
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.movie-export-', dir=destination.parent) as directory:
        temporary = Path(directory) / ('movie' + destination.suffix.lower())
        _run([executable, '-nostdin', '-v', 'error', '-threads', '2', '-filter_complex_threads', '1',
              '-framerate', str(metadata['fps']), '-i', str(Path(frames) / '%04d.png'),
              '-filter_complex', filters, '-map', '[v]', '-frames:v', str(metadata['frames']),
              *codec, str(temporary)], deadline=deadline, cancelled=cancelled)
        temporary.replace(destination)


def package_frames(directory, archive, config, width, height):
    paths = sorted(Path(directory).glob('*.png'))
    metadata = dict(version=1, fps=config['fps'], frames=len(paths), width=width, height=height,
                    animation=config, files=[dict(name=p.name, size=p.stat().st_size,
                        sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths])
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as out:
        out.writestr('frames.json', json.dumps(metadata))
        for path in paths:
            out.write(path, path.name)
    if Path(archive).stat().st_size > MAX_ARCHIVE:
        raise ValueError('Frame archive exceeds 512 MiB')
    return metadata


@contextmanager
def unpack(archive):
    """Validate all members and PNG headers before exposing paths to PyMOL/FFmpeg."""
    archive = Path(archive)
    if archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError('Frame archive exceeds 512 MiB')
    with tempfile.TemporaryDirectory(prefix='blender-movie-') as directory, zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > MAX_FRAMES + 1 or len({i.filename for i in members}) != len(members):
            raise ValueError('Invalid frame archive member count')
        if sum(i.file_size for i in members) > MAX_ARCHIVE or any(i.is_dir() or i.flag_bits & 1 or (i.external_attr >> 16) & 0o170000 == 0o120000 for i in members):
            raise ValueError('Invalid frame archive sizes or members')
        info = source.getinfo('frames.json')
        if info.file_size > 128 * 1024:
            raise ValueError('Frame metadata too large')
        meta = json.loads(source.read(info))
        if not isinstance(meta, dict) or meta.get('version') != 1 or any(type(meta.get(k)) is not int for k in ('fps', 'frames', 'width', 'height')):
            raise ValueError('Invalid movie metadata')
        n, w, h = meta['frames'], meta['width'], meta['height']
        if not 4 <= n <= MAX_FRAMES or not 1 <= meta['fps'] <= 60 or n / meta['fps'] > 15 or min(w, h) < 1 or n*w*h*4 > MAX_RGBA:
            raise ValueError('Movie exceeds frame, FPS or memory bounds')
        names = [f'{i:04d}.png' for i in range(n)]
        if {i.filename for i in members} != set(names) | {'frames.json'}:
            raise ValueError('Unexpected frame archive paths')
        records = meta.get('files')
        if not isinstance(records, list) or len(records) != n or any(not isinstance(i, dict) for i in records) or [i.get('name') for i in records] != names:
            raise ValueError('Invalid frame ordering')
        for record in records:
            raw = source.read(record['name'])
            if len(raw) != record.get('size') or hashlib.sha256(raw).hexdigest() != record.get('sha256'):
                raise ValueError('Frame checksum mismatch')
            if len(raw) < 33 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR' or struct.unpack('>II', raw[16:24]) != (w, h):
                raise ValueError('Invalid frame PNG dimensions')
            from PIL import Image
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
            except (OSError, ValueError) as exc:
                raise ValueError('Invalid frame PNG data') from exc
            (Path(directory) / record['name']).write_bytes(raw)
        yield Path(directory), meta


def export(archive, destination, **kwargs):
    with unpack(archive) as (frames, metadata):
        encode(frames, metadata, destination, **kwargs)
    return str(Path(destination).resolve())
