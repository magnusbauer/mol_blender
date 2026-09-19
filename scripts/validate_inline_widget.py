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
 for mode in ['initial','notification','wrapped','globals','missing','invalid','gif','gif_url','bad_url','downloads']:
  page=browser.new_page();requests=[];page.on('request',lambda r: requests.append(r.url))
  if mode=='initial':page.add_init_script('window.openai='+json.dumps({'toolResponseMetadata':{'mcp_tool_result':{'_meta':{'preview':preview}}}}))
  page.goto('about:blank');page.set_content(html)
  result={'_meta':{'preview':preview},'structuredContent':{'complete':True}}
  if mode in ('gif', 'gif_url'):
   gif=io.BytesIO()
   Image.new('RGB',(80,60),'red').save(gif,format='GIF',save_all=True,
       append_images=[Image.new('RGB',(80,60),'blue')],duration=200,loop=0)
   result['_meta']['preview']={'mimeType':'image/gif','data':base64.b64encode(gif.getvalue()).decode()}
  if mode == 'gif_url':
   page.route('https://render.example.org/preview/test', lambda route: route.fulfill(content_type='image/gif',body=gif.getvalue()))
   result['_meta']['preview']={'mimeType':'image/gif','url':'https://render.example.org/preview/test'}
  if mode == 'bad_url':
   result['_meta']['preview']={'mimeType':'image/gif','url':'javascript:alert(1)'}
  if mode == 'downloads':
   result['_meta']['downloads']=[{'name':'render.'+ext,'url':'https://render.example.org/download/'+ext} for ext in ('gif','mp4','png')]+[{'name':'render.gif','url':'javascript:alert(1)'}]
   page.evaluate('window.openai={openExternal:({href})=>window.clicked=href}')
  if mode in ('notification','missing','invalid','gif','gif_url','bad_url','downloads'):


   if mode=='missing':result['_meta']['preview']=None
   if mode=='invalid':result['_meta']['preview']={'mimeType':'image/png','data':'https://evil.invalid/image.png'}
   page.evaluate("r => window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')",result)
  if mode in ('wrapped','globals'):
   data={'call_tool_result':result} if mode=='wrapped' else result['_meta']
   page.evaluate("r=>window.dispatchEvent(new CustomEvent('openai:set_globals',{detail:{globals:{toolResponseMetadata:r}}}))",data)
  if mode in ('missing','invalid','bad_url'):
   page.wait_for_function("document.getElementById('status').textContent.includes('unavailable')")
   assert page.locator('#preview').is_hidden()
  else:
   page.wait_for_function("!document.getElementById('preview').hidden && document.getElementById('preview').naturalWidth===80")
   assert page.locator('#status').is_hidden()
  assert requests == (['https://render.example.org/preview/test'] if mode == 'gif_url' else []), requests
  if mode == 'downloads':
   assert page.locator('#downloads a').count()==3
   page.get_by_text('Download GIF',exact=True).click()
   assert page.evaluate('window.clicked')=='https://render.example.org/download/gif'
  page.close();print(mode,'passed')
 # Saving starts once automatically; failed uploads require an explicit retry.
 for fail in (False, True):
  page=browser.new_page()
  page.set_content(html)
  page.route('https://render.example.org/preview/save',lambda route: route.fulfill(content_type='image/png',body=buf.getvalue()))
  page.evaluate("""fail => {window.openai={
    uploadFile:async file=>{window.uploadCount=(window.uploadCount||0)+1;window.uploaded={name:file.name,size:file.size,type:file.type}; if(fail) throw Error('unavailable'); return {fileId:'file-saved'};},
    setWidgetState:state=>window.savedState=state
  }}""",fail)
  page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')",
    {'_meta':{'preview':preview,'saveImage':{'jobId':'test-job','url':'https://render.example.org/preview/save'}}})
  if fail:
   page.wait_for_function("document.getElementById('saved').textContent.includes('not saved')")
   assert page.evaluate('window.savedState') is None
  else:
   page.wait_for_function("window.savedState?.imageIds?.[0]==='file-saved'")
   assert page.evaluate('window.uploaded.type')=='image/png'
   state=page.evaluate('window.savedState')
  assert page.evaluate('window.uploadCount') == 1
  page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')",
    {'_meta':{'saveImage':{'jobId':'test-job','url':'https://render.example.org/preview/save'}}})
  page.wait_for_timeout(100)
  assert page.evaluate('window.uploadCount') == 1
  if fail:
   page.get_by_text('Retry saving image',exact=True).click()
   page.wait_for_function('window.uploadCount===2')
  page.close()
 page=browser.new_page()
 page.route('https://files.oaiusercontent.com/saved.png',lambda route: route.fulfill(content_type='image/png',body=buf.getvalue()))
 page.goto('about:blank')
 page.evaluate("state=>window.openai={widgetState:state,getFileDownloadUrl:async({fileId})=>{window.restoredId=fileId;return {downloadUrl:'https://files.oaiusercontent.com/saved.png'}}}",state)
 page.set_content(html)
 page.wait_for_function("document.getElementById('preview').naturalWidth===80")
 assert page.evaluate('window.restoredId')=='file-saved'
 page.close();print('automatic save, duplicate suppression, explicit retry, and restore passed')
 # Exercise the actual parent/iframe initialization sequence, not only globals.
 page=browser.new_page()
 page.set_content('<iframe id="app" style="height:1000px" sandbox="allow-scripts"></iframe>')
 page.evaluate("""({html, result}) => {
   window.ready = false; window.pong = false;
   const frame = document.getElementById('app');
   window.addEventListener('message', event => {
     if (event.source !== frame.contentWindow) return;
     const m = event.data;
     if (m.method === 'ui/initialize') frame.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,result:{protocolVersion:'2026-01-26',hostCapabilities:{}}}, '*');
     if (m.method === 'ui/notifications/initialized') {
       window.ready = true;
       frame.contentWindow.postMessage({jsonrpc:'2.0',id:'probe',method:'ping'}, '*');
       frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:result}, '*');
     }
     if (m.method === 'ui/notifications/size-changed') window.measuredHeight=m.params.height;
     if (m.id === 'probe' && m.result) window.pong = true;
   });
   frame.srcdoc = html;
 }""", {'html':html,'result':{'_meta':{'preview':preview}}})
 page.wait_for_function('window.ready && window.pong')
 frame=page.frames[1]
 frame.wait_for_function("!document.getElementById('preview').hidden && document.getElementById('preview').naturalWidth===80")
 page.wait_for_function('window.measuredHeight > 0 && window.measuredHeight < 500')
 page.close();print('sandbox handshake and ping passed')
 browser.close()
