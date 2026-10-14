"""Real Chromium with a simulated host; opt in with --browser PATH."""
import base64
import io
import json

import pytest
from PIL import Image


@pytest.fixture(scope="session")
def browser(request):
    executable = request.config.getoption("--browser")
    if not executable:
        pytest.skip("Pass --browser to execute the simulated-host browser checks")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(executable_path=executable, headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page_factory(browser):
    contexts = []
    def create():
        context = browser.new_context()
        contexts.append(context)
        # Every external response in these tests must be supplied explicitly.
        context.route("**/*", lambda route: route.abort())
        return context.new_page()
    yield create
    for context in contexts:
        context.close()


@pytest.fixture
def page(page_factory):
    return page_factory()


@pytest.fixture
def html():
    from blender_render.viewer import inline_viewer_html
    return inline_viewer_html()


@pytest.fixture
def buf():
    data = io.BytesIO()
    Image.new("RGBA", (80, 60), (0, 220, 170, 160)).save(data, format="PNG")
    return data


@pytest.fixture
def preview(buf):
    return {"mimeType": "image/png", "data": base64.b64encode(buf.getvalue()).decode()}


HOST_BRIDGE = """
window.installHostBridge = function(target, capabilities, reject=false, repeats=0, result=null) {
  window.calls=[]; window.ready=false; window.pong=false;
  const reply=message=>target.postMessage({jsonrpc:'2.0',...message},'*');
  window.addEventListener('message', event => {
    if(event.source!==target) return;
    const m=event.data;
    if(m.method==='ui/initialize') reply({id:m.id,result:{
      protocolVersion:'2026-01-26',hostCapabilities:capabilities}});
    if(m.method==='ui/notifications/initialized') {
      window.ready=true;
      reply({id:'probe',method:'ping'});
      for(let i=0;i<repeats;i++) reply({method:'ui/notifications/tool-result',params:result});
    }
    if(m.method==='ui/notifications/size-changed') window.measuredHeight=m.params.height;
    if(m.id==='probe' && m.result) window.pong=true;
    if(['ui/message','ui/update-model-context'].includes(m.method)) {
      window.calls.push(m);
      reply({id:m.id,result:reject?{isError:true}:{}});
    }
  });
}
"""


@pytest.fixture
def host(page):
    """Mount the actual viewer with configurable capabilities and RPC replies."""
    def mount(html, result, *, capabilities=None, reject=False, repeats=1):
        page.set_content('<iframe id="app" style="height:1000px" sandbox="allow-scripts"></iframe>')
        page.evaluate(HOST_BRIDGE)
        page.evaluate("""({html,result,capabilities,reject,repeats}) => {
          const frame=document.getElementById('app');
          installHostBridge(frame.contentWindow,capabilities,reject,repeats,result);
          frame.srcdoc=html;
        }""", {"html": html, "result": result, "capabilities": capabilities or {},
                 "reject": reject, "repeats": repeats})
        page.wait_for_function("window.ready && window.pong")
        return page.frames[1]
    return mount


@pytest.fixture
def file_host(page):
    """One file-storage fake, with per-file failure and state-commit controls."""
    files = {}
    def install(*, rejected=(), reject_state=False, target=None, before_load=False,
                result=None, state=None, selection=(), no_api=False, file_id=None):
        target = target or page
        def upload(data, name):
            saved_id = file_id or 'saved-' + name
            files[saved_id] = base64.b64decode(data)
            return {'fileId': saved_id}
        target.expose_function('captureUpload', upload)
        config = {'rejected': list(rejected), 'reject_state': reject_state, 'result': result,
                  'state': state, 'selection': list(selection), 'no_api': no_api}
        script = """(cfg => {
          window.counts={}; window.uploads=[]; window.savedState=null;
          window.fileReads=[]; window.selection=cfg.selection; window.downloadUrl=null;
          window.rejected=cfg.rejected; window.rejectState=cfg.reject_state;
          window.openai={toolResponseMetadata:cfg.result,widgetState:cfg.state};
          if (cfg.no_api) return;
          Object.assign(window.openai, {
            selectFiles:async()=>window.selection,
            getFileDownloadUrl:async({fileId})=>{
              window.fileReads.push(fileId);
              return {downloadUrl:window.downloadUrl || 'https://files.oaiusercontent.com/' + encodeURIComponent(fileId)};
            },
            uploadFile:async (file,options)=>{
              window.uploadOptions=options;
              window.uploaded={name:file.name,size:file.size,type:file.type};
              window.counts[file.name]=(window.counts[file.name]||0)+1;
              window.uploads.push(file.name);
              if(window.rejected.includes(file.name)) throw Error('format unavailable');
              const bytes=new Uint8Array(await file.arrayBuffer());
              return window.captureUpload(btoa(Array.from(bytes,x=>String.fromCharCode(x)).join('')),file.name);
            },
            setWidgetState:state=>{
              if(window.rejectState) throw Error('state unavailable');
              window.savedState=structuredClone(state);
            }
          });
        })(""" + json.dumps(config) + ");"
        if before_load:
            target.add_init_script(script)
        else:
            target.evaluate(script)
    install.files = files
    return install


@pytest.fixture
def library_host(page_factory, file_host, html):
    """Fresh contexts share only the simulated host's saved bytes, never widget state."""
    from urllib.parse import unquote, urlsplit
    def mount(*, result=None, state=None, denied=False, no_api=False, selection=None):
        page = page_factory()
        requests = []
        page.on('request', lambda request: requests.append(request.url))
        page.route('https://render.example.org/**', lambda route: route.fulfill(status=410))
        page.route('https://viewer.example.org/', lambda route: route.fulfill(
            content_type='text/html', body=html,
            headers={'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data: https://*.oaiusercontent.com; connect-src https://*.oaiusercontent.com"}))
        def download(route):
            file_id = unquote(urlsplit(route.request.url).path[1:])
            headers = {'Access-Control-Allow-Origin': '*'}
            if denied or file_id not in file_host.files:
                route.fulfill(status=403, headers=headers)
            else:
                route.fulfill(content_type='image/png', body=file_host.files[file_id], headers=headers)
        page.route('https://files.oaiusercontent.com/**', download)
        file_host(target=page, before_load=True, result=result, state=state, no_api=no_api,
                  file_id='sediment://file_library_exact_id', selection=selection if selection is not None else [
                      {'fileId':'sediment://file_library_exact_id', 'fileName':'blender-library-trial.png', 'mimeType':'image/png'}])
        capabilities = {'message': {'image': True}, 'updateModelContext': {'image': True}}
        page.add_init_script(HOST_BRIDGE + '\ninstallHostBridge(window,' + json.dumps(capabilities) + ');')
        page.goto('https://viewer.example.org/')
        page.evaluate("caps=>window.postMessage({jsonrpc:'2.0',id:'initialize',result:{hostCapabilities:caps}},'*')", capabilities)
        page.wait_for_function('window.ready')
        return page, requests
    return mount
