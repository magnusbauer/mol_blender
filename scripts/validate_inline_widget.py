"""Exercise the packaged viewer in Chromium with simulated host envelopes.

Requires Playwright and a local Chrome/Chromium executable. This is a real browser
check, not evidence of ChatGPT host integration or durable conversation storage.
"""
import argparse
import base64,io,json
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright
buf=io.BytesIO();Image.new('RGBA',(80,60),(0,220,170,160)).save(buf,format='PNG')
preview={'mimeType':'image/png','data':base64.b64encode(buf.getvalue()).decode()}
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--browser', required=True)
args=parser.parse_args()
import blender_render
html=Path(blender_render.__file__).with_name('inline_preview.html').read_text()
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox'])
 for mode in ['initial','notification','wrapped','globals','missing','invalid','gif']:
  page=browser.new_page();requests=[];page.on('request',lambda r: requests.append(r.url))
  if mode=='initial':page.add_init_script('window.openai='+json.dumps({'toolResponseMetadata':{'mcp_tool_result':{'_meta':{'preview':preview}}}}))
  page.goto('about:blank');page.set_content(html)
  result={'_meta':{'preview':preview},'structuredContent':{'complete':True}}
  if mode == 'gif':
   gif=io.BytesIO()
   Image.new('RGB',(80,60),'red').save(gif,format='GIF',save_all=True,
       append_images=[Image.new('RGB',(80,60),'blue')],duration=200,loop=0)
   result['_meta']['preview']={'mimeType':'image/gif','data':base64.b64encode(gif.getvalue()).decode()}
  if mode in ('notification','missing','invalid','gif'):
   if mode=='missing':result['_meta']['preview']=None
   if mode=='invalid':result['_meta']['preview']={'mimeType':'image/png','data':'https://evil.invalid/image.png'}
   page.evaluate("r => window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')",result)
  if mode in ('wrapped','globals'):
   data={'call_tool_result':result} if mode=='wrapped' else result['_meta']
   page.evaluate("r=>window.dispatchEvent(new CustomEvent('openai:set_globals',{detail:{globals:{toolResponseMetadata:r}}}))",data)
  if mode in ('missing','invalid'):
   page.wait_for_function("document.getElementById('status').textContent.includes('unavailable')")
   assert page.locator('#preview').is_hidden()
  else:
   page.wait_for_function("!document.getElementById('preview').hidden && document.getElementById('preview').naturalWidth===80")
   assert page.locator('#status').is_hidden()
  assert not requests,requests
  page.close();print(mode,'passed')
 browser.close()
