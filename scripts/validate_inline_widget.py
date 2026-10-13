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
 # File APIs absent initially must be visible, then recover without another result.
 page=browser.new_page();page.set_content(html)
 page.route('https://render.example.org/preview/late',lambda route:route.fulfill(content_type='image/png',body=buf.getvalue()))
 page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')",
   {'_meta':{'saveFiles':[{'name':'render.png','jobId':'late','url':'https://render.example.org/preview/late'}]}})
 page.wait_for_function("document.getElementById('saved').textContent.includes('File saving is unavailable')")
 assert page.locator('#saved').is_visible()
 page.evaluate("window.openai={uploadFile:async()=>({fileId:'file-late'}),setWidgetState:s=>window.savedState=s}")
 page.locator('#save').click()
 page.wait_for_function("window.savedState?.privateContent?.savedFiles?.['render.png']?.fileId==='file-late'")
 assert page.locator('#save').is_visible()
 page.close();print('missing and delayed upload APIs passed')
 # Saving is explicit and independent of model visibility; failures require a retry.
 for fail in (False, True):
  page=browser.new_page()
  page.set_content(html)
  page.route('https://render.example.org/preview/save',lambda route: route.fulfill(content_type='image/png',body=buf.getvalue()))
  page.evaluate("""fail => {window.openai={
    uploadFile:async (file,options)=>{window.uploadOptions=options;window.uploadCount=(window.uploadCount||0)+1;window.uploaded={name:file.name,size:file.size,type:file.type}; if(fail) throw Error('unavailable'); return {fileId:'file-saved'};},
    setWidgetState:state=>window.savedState=state
  }}""",fail)
  page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')",
    {'_meta':{'preview':preview,'saveImage':{'jobId':'test-job','url':'https://render.example.org/preview/save'}}})
  assert page.evaluate('window.uploadCount || 0') == 0
  page.locator('#save').click()
  if fail:
   page.wait_for_function("document.getElementById('saved').textContent.includes('not saved')")
   assert page.evaluate('window.savedState') is None
  else:
   page.wait_for_function("window.savedState?.privateContent?.savedFiles?.['render.png']?.fileId==='file-saved'")
   assert page.evaluate('window.uploaded.type')=='image/png'
   state=page.evaluate('window.savedState')
  assert page.evaluate('window.uploadOptions') == {'library':True}
  assert page.evaluate('window.uploadCount') == 1
  page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')",
    {'_meta':{'saveImage':{'jobId':'test-job','url':'https://render.example.org/preview/save'}}})
  page.wait_for_timeout(100)
  assert page.evaluate('window.uploadCount') == 1
  if fail:
   page.get_by_text('Retry saving files',exact=True).click()
   page.wait_for_function('window.uploadCount===2')
  page.close()
 page=browser.new_page()
 page.route('https://files.oaiusercontent.com/saved.png',lambda route: route.fulfill(content_type='image/png',body=buf.getvalue()))
 page.goto('about:blank')
 page.evaluate("state=>window.openai={widgetState:state,getFileDownloadUrl:async({fileId})=>{window.restoredId=fileId;return {downloadUrl:'https://files.oaiusercontent.com/saved.png'}}}",state)
 page.set_content(html)
 page.wait_for_function("document.getElementById('preview').naturalWidth===80")
 assert page.evaluate('window.restoredId')=='file-saved'
 page.close();print('explicit save, duplicate suppression, retry, and restore passed')
 # Multi-file partial failure: a retry must not re-upload successful formats.
 page=browser.new_page();page.set_content(html)
 for ext,mime in [('png','image/png'),('gif','image/gif'),('mp4','video/mp4')]:
  page.route('https://render.example.org/preview/'+ext,lambda route,request,mime=mime:route.fulfill(content_type=mime,body=b'file-test-bytes'))
 page.evaluate("""() => {window.counts={};window.rejectVideo=true;window.openai={
  uploadFile:async file=>{window.counts[file.name]=(window.counts[file.name]||0)+1;if(file.type==='video/mp4'&&window.rejectVideo)throw Error('format unavailable');return {fileId:'file-'+file.name};},
  setWidgetState:state=>window.savedState=state
 }}""")
 files=[{'jobId':'movie','name':'render.'+ext,'mimeType':mime,'url':'https://render.example.org/preview/'+ext} for ext,mime in [('png','image/png'),('gif','image/gif'),('mp4','video/mp4')]]
 page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')",{'_meta':{'saveFiles':files}})
 page.locator('#save').click()
 page.wait_for_function("document.getElementById('saved').textContent.includes('render.mp4 not saved')")
 assert set(page.evaluate('Object.keys(window.savedState.privateContent.savedFiles)'))=={'render.png','render.gif'}
 assert page.evaluate('window.savedState.imageIds') is None
 page.evaluate('window.rejectVideo=false')
 page.get_by_text('Retry saving files',exact=True).click()
 page.wait_for_function("window.savedState.privateContent.savedFiles['render.mp4']?.fileId")
 assert page.evaluate('window.counts')=={'render.png':1,'render.gif':1,'render.mp4':2}
 page.close();print('PNG/GIF/MP4 partial save and retry passed')
 # Every output survives a state-only reopen, including late state hydration.
 formats={'render.png':'image/png','render.gif':'image/gif','render.mp4':'video/mp4',
          'render.blend':'application/octet-stream','manifest.json':'application/json','frames.zip':'application/zip'}
 page=browser.new_page();page.set_content(html)
 for name,mime in formats.items():
  page.route('https://render.example.org/download/'+name+'?delivery=ack',
    lambda route,request,mime=mime:route.fulfill(content_type=mime,body=buf.getvalue()))
 page.evaluate("""() => {window.uploads=[];window.rejectState=true;window.openai={
   uploadFile:async file=>{window.uploads.push(file.name);return {fileId:'saved-'+file.name};},
   setWidgetState:async state=>{if(window.rejectState)throw Error('state unavailable');window.savedState=state;}
 }}""")
 files=[{'jobId':'all','name':name,'url':'https://render.example.org/download/'+name+'?delivery=ack'} for name in formats]
 page.evaluate("r=>window.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*')",{'_meta':{'saveFiles':files}})
 page.locator('#save').click()
 page.wait_for_function("document.getElementById('saved').textContent.includes('not saved')")
 assert page.evaluate('window.savedState') is None
 assert 'saved in ChatGPT' not in page.locator('#saved').inner_text()
 page.evaluate('window.rejectState=false')
 page.get_by_text('Retry saving files',exact=True).click()
 page.wait_for_function("document.getElementById('save').textContent==='Files saved'")
 assert page.evaluate('window.uploads')==list(formats) # State retry reuses uploaded IDs.
 state=page.evaluate('window.savedState')
 assert set(state['privateContent']['savedFiles'])==set(formats)
 page.close()
 for late in (False,True):
  page=browser.new_page();requests=[];page.on('request',lambda r:requests.append(r.url))
  page.route('https://files.oaiusercontent.com/restored',lambda route:route.fulfill(content_type='image/png',body=buf.getvalue()))
  page.goto('about:blank')
  page.evaluate("""state=>window.openai={widgetState:state,
   getFileDownloadUrl:async({fileId})=>{window.restoredId=fileId;return {downloadUrl:'https://files.oaiusercontent.com/restored'};},
   openExternal:({href})=>window.opened=href}""",None if late else state)
  page.set_content(html)
  if late:
   page.evaluate("state=>window.dispatchEvent(new CustomEvent('openai:set_globals',{detail:{globals:{widgetState:state}}}))",state)
  page.wait_for_function("document.getElementById('preview').naturalWidth===80")
  assert page.evaluate('window.restoredId')=='saved-render.gif'
  assert page.locator('#downloads a').count()==6
  page.get_by_text('Download ZIP',exact=True).click()
  page.wait_for_function("window.restoredId==='saved-frames.zip'")
  assert page.evaluate('window.opened')=='https://files.oaiusercontent.com/restored'
  assert all(url.startswith('https://files.oaiusercontent.com/') for url in requests)
  page.close()
 print('all outputs, failed state commit retry, and immediate/delayed state-only reopen passed')
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
 # Model image delivery uses the standard bridge with exact image bytes.
 for reject in (False, True):
  page=browser.new_page()
  page.set_content('<iframe id="app" style="height:1000px" sandbox="allow-scripts"></iframe>')
  result={'_meta':{'preview':preview},'structuredContent':{'job_id':'cpu-test'}}
  page.evaluate("""({html,result,reject}) => {
    window.calls=[];
    const frame=document.getElementById('app');
    window.addEventListener('message', e=>{
      if(e.source!==frame.contentWindow) return;
      const m=e.data;
      if(m.method==='ui/initialize') frame.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,result:{hostCapabilities:{message:{image:true},updateModelContext:{image:true}}}},'*');
      if(m.method==='ui/notifications/initialized') {
        frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:result},'*');
        frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:result},'*');
      }
      if(['ui/message','ui/update-model-context'].includes(m.method)) {
        window.calls.push(m);
        frame.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,result:reject?{isError:true}:{}},'*');
      }
    });
    frame.srcdoc=html;
  }""",{'html':html,'result':result,'reject':reject})
  frame=page.frames[1]
  page.wait_for_function('window.calls.length===1')
  frame.wait_for_function("document.getElementById('handoff').textContent.includes('confirmed' )" if reject else "document.getElementById('handoff').textContent.includes('supplied as context')")
  calls=page.evaluate('window.calls')
  assert calls[0]['method']=='ui/update-model-context'
  assert calls[0]['params']['content'][1]=={'type':'image',**preview}
  frame.locator('#share').click()
  page.wait_for_function('window.calls.length===2')
  frame.wait_for_function("document.getElementById('handoff').textContent.includes('unconfirmed')" if reject else "document.getElementById('share').textContent==='Image sent to ChatGPT'")
  calls=page.evaluate('window.calls')
  assert calls[1]['method']=='ui/message'
  assert calls[1]['params']['role']=='user'
  assert calls[1]['params']['content'][1]=={'type':'image',**preview}
  assert frame.locator('#share').is_enabled()==reject
  # Context failure never starts a retry loop or an automatic chat message.
  page.wait_for_timeout(100)
  assert len(page.evaluate('window.calls'))==2
  page.close()
 print('standard image context, explicit image message, duplicate suppression and rejection passed')
 browser.close()
