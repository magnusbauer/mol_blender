"""Run with PyMOL's Python; real render acceptance with portable output paths."""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
os.environ['MOL_BLENDER_HOME'] = str(root/'.runtime')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=root/'.validation'/('windows-'+uuid.uuid4().hex[:8]))
    parser.add_argument('--cpu', action='store_true')
    parser.add_argument('--animation', action='store_true')
    args = parser.parse_args()
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    ffmpeg_bin = Path(sys.executable).parent/'Library/bin'
    if (ffmpeg_bin/'ffmpeg.exe').is_file():
        os.environ['PATH'] = str(ffmpeg_bin) + os.pathsep + os.environ.get('PATH', '')
    import pymol2
    from mol_blender.pymol_bridge_plugin import register_plugin
    with pymol2.PyMOL() as session:
        cmd = session.cmd
        register_plugin(cmd)
        cmd.load(str(root/'examples/1sps.cif'), 'sample')
        cmd.orient()
        cmd.turn('y', 25)
        before = cmd.get_view()
        extra = ''
        if args.cpu:
            overrides = output/'overrides.json'
            overrides.write_text(json.dumps({'renderer':{'device':'CPU'}}), encoding='utf-8')
            extra += ', overrides_json=' + overrides.as_posix()
        if args.animation:
            extra += ', animation=rock, duration=1, fps=4, gif=1, auto_preview=0'
        command = ('mol_blender width=320, height=240, samples=8, save_blend=1, output_dir=' +
                   output.as_posix() + ', output_name=pymol-command.png' + extra)
        cmd.do(command)
        assert (output/'pymol-command.png').is_file(), 'PyMOL command did not produce an image'
        assert tuple(cmd.get_view()) == tuple(before), 'Rendering changed the PyMOL view'
        manifest = json.loads((output/'pymol-command.render/manifest.json').read_text())
        if args.cpu:
            assert manifest['cycles_device']['device'] == 'CPU'
        if args.animation:
            for suffix in ('mp4', 'gif', 'frames.zip'):
                assert (output/('pymol-command.'+suffix)).is_file()
        else:
            cmd.png(str(output/'pymol-saved.png'), ray=0)
            assert (output/'pymol-saved.png').is_file()
        print('PYMOL_COMMAND_VERIFIED', cmd.get_version(), manifest['cycles_device'], output)


if __name__ == '__main__':
    main()
