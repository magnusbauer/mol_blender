"""Render public 1QYS through consumer admission and verify native GIF handoff.

Requires an installed package, real GPU runtime image, PyMOL, and network access
to RCSB. Local MCP authentication is stubbed; this does not attest hosted OAuth
or ChatGPT UI behavior. Outputs remain in the requested validation directory.
"""
import argparse
import json,os,sys,time,subprocess,urllib.request,asyncio,base64
from pathlib import Path
from blender_render.jobs import JobStore,Policy
from blender_render.service import RenderService,HostBackend
from blender_render import consumer,mcp_server
from blender_render.api import Downloads
from fastmcp import Client
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--image',required=True)
parser.add_argument('--pymol-python',required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
data=urllib.request.urlopen('https://files.rcsb.org/download/1QYS.cif',timeout=30).read()
def convert(data,suffix):
 source=root/'1qys.cif';source.write_bytes(data)
 target=root/'bundle.json'
 subprocess.run([args.pymol_python,'-m','blender_render.export',str(source),str(target)],env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),check=True,stdout=subprocess.DEVNULL)
 return json.loads(target.read_text())
backend=HostBackend(root/'worker',blender='blender',image=args.image,timeout=600)
store=JobStore(root/'controller',Policy(allow_animation=True))
service=RenderService(store,backend,converter=convert)
try:
 req={'request_key':'public-1qys-spin','preset':'neon_duplex_hero','animation':'360','gif':True}
 started=time.monotonic();plan=service.consumer_preflight('validation',req)
 job=service.admit_consumer_file('validation',data,'.cif','1qys.cif',convert,plan)
 service._execute(store.claim())
 status=store.get('validation',job['job_id']);print('status',status['status'],flush=True)
 assert status['status']=='done',status
 mcp_server.owner=lambda:'validation'
 mcp=mcp_server.create_http(service,auth=StaticTokenVerifier(tokens={}),downloads=Downloads('x'*48,'https://render.example.org'),converter=convert)
 async def result():
  async with Client(mcp) as client:return await client.call_tool('render_result',{'job_id':job['job_id']})
 result=asyncio.run(result())
 assert result.meta['preview']['mimeType']=='image/gif'
 assert [x.name for x in result.content if x.type=='resource_link']==['render.gif']
 for name in ('render.png','render.gif','render.mp4','manifest.json'):
  path,_=service.artifact('validation',job['job_id'],name);(root/name).write_bytes(path.read_bytes())
 from PIL import Image
 with Image.open(root/'render.gif') as im:
  assert im.n_frames==36 and im.info['loop']==0
 with Image.open(root/'render.png') as im:
  assert im.convert('RGBA').getchannel('A').getextrema()==(255,255)
 evidence={'seconds':time.monotonic()-started,'frames':36,'size':[640,360],'samples':32,'gif_bytes':(root/'render.gif').stat().st_size,'stage_history':status.get('stage_history'),'platform':'local Apptainer GPU; in-process MCP; not hosted OAuth or ChatGPT'}
 (root/'evidence.json').write_text(json.dumps(evidence,indent=2));print(json.dumps(evidence),flush=True)
finally: backend.stop();store.close()
