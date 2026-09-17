"""Executed public-fixture animation acceptance using one real warm GPU worker.

Run from a built wheel environment with --gallery pointing to the checkout's
examples/presets/gallery.json. Artifacts and source structures stay in --output.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gallery',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--pymol-python',required=True)
    parser.add_argument('--preset',action='append')
    parser.add_argument('--transparent',action='store_true')
    args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    from blender_render.jobs import JobStore,Policy
    from blender_render.service import RenderService,HostBackend
    from blender_render.artifacts import destination
    from blender_render.animation import unpack
    from blender_render.engine import engine_identity
    backend=HostBackend(root/'worker',blender='blender',image=args.image,timeout=900)
    store=JobStore(root/'controller',Policy(max_job_seconds=900))
    service=RenderService(store,backend)
    catalog=json.loads(args.gallery.read_text())
    evidence={'engine':engine_identity(),'cases':[]}
    try:
        for row in catalog:
            preset=row['preset']
            if args.preset and preset not in args.preset: continue
            structure=row['source_id'].lower()
            cif=root/(structure+'.cif');bundle=root/(structure+'.json')
            if not cif.exists():
                urllib.request.urlretrieve('https://files.rcsb.org/download/'+structure.upper()+'.cif',cif)
                subprocess.run([args.pymol_python,'-m','blender_render.export',str(cif),str(bundle)],
                               env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),check=True)
            payload=json.loads(bundle.read_text())
            source=service.import_payload('animation-validation',payload)['input_id']
            case=root/preset;case.mkdir()
            options=dict(preset=preset,animation='360',duration=1,fps=4,gif=True,
                         width=320,height=240,samples=8,preserve_view=False)
            if preset=='preset_1_interface': options['animation']='rock'
            if args.transparent: options['transparent_background']=True
            print('START',preset,flush=True)
            started=time.monotonic()
            job=service.submit('animation-validation',source,options,preset)
            service._execute(store.claim())
            status=store.get('animation-validation',job['job_id'])
            if status['status']!='done':
                raise RuntimeError(str(status))
            result=store.result('animation-validation',job['job_id'])
            for item in result['artifacts']:
                origin,_=service.artifact('animation-validation',job['job_id'],item['name'])
                target=destination(case/'render.png',item['name'])
                target.write_bytes(origin.read_bytes())
            with unpack(case/'render.frames.zip') as (frames,meta):
                assert meta['frames']==4
                if args.transparent:
                    from PIL import Image
                    with Image.open(frames/'0000.png') as image:
                        assert image.getchannel('A').getextrema()[0] == 0
            for ext in ('mp4','gif'):
                stream=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames',
                    '-show_streams','-of','json',str(case/('render.'+ext))]))['streams'][0]
                assert int(stream['nb_read_frames'])==4
            evidence['cases'].append(dict(preset=preset,structure=structure,job_id=job['job_id'],
                seconds=time.monotonic()-started,transparent=args.transparent,artifacts=result['artifacts']))
            (root/'evidence.json').write_text(json.dumps(evidence,indent=2))
            print('PASS',preset,flush=True)
    finally:
        backend.stop();store.close()


if __name__=='__main__': main()
