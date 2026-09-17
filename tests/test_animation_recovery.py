"""Animation uses the existing owned job and recoverable artifact lifecycle."""
import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blender_render.api import create_app
from blender_render.client import ClientError
from blender_render.jobs import JobStore, Policy
from blender_render.service import RenderService
from test_jobs_api import Compute, payload, TOKEN
from test_result_recovery import LocalClient
from test_cli_operation_routing import no_live_boundaries


class MovieCompute(Compute):
    def render(self, request, *args):
        result=super().render(request,*args)
        result.update(mp4_base64=base64.b64encode(b'fake-mp4').decode(),
                      gif_base64=base64.b64encode(b'GIF89a-fake').decode(),
                      frames_base64=base64.b64encode(b'fake-archive').decode())
        result['manifest']['resolved_preset']=request['style_doc']
        return result


class MovieClient(LocalClient):
    interrupt=False
    def request(self,method,path,*args,**kwargs):
        if self.interrupt and method=='GET' and '/artifacts/frames.zip' in path:
            raise ClientError('interrupted frames download')
        return super().request(method,path,*args,**kwargs)


def test_movie_partial_delivery_restart_and_no_redispatch(tmp_path,payload,monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME',str(tmp_path/'client'))
    root=tmp_path/'state'
    store=JobStore(root);compute=MovieCompute();service=RenderService(store,compute)
    source=service.import_payload('owner',payload)['input_id']
    job=service.submit('owner',source,{'animation':'rock','gif':True,'width':320,'height':240},'movie')
    identifier=job['job_id'];service._execute(store.claim())
    api=TestClient(create_app(service,token=TOKEN,tokens={TOKEN:'owner'}))
    client=MovieClient(api);client.interrupt=True
    output=tmp_path/'out.png'
    first=client.download(identifier,output)
    assert not first['complete'] and first['missing_required']==['frames.zip']
    assert set(first['outputs']) >= {'render.png','render.mp4','render.gif'}
    api.close();store.close()
    store=JobStore(root);service=RenderService(store,compute)
    api=TestClient(create_app(service,token=TOKEN,tokens={TOKEN:'owner'}))
    client=MovieClient(api)
    second=client.download(identifier,output)
    assert second['complete']
    assert Path(second['outputs']['frames.zip'])==output.with_suffix('.frames.zip')
    assert compute.calls==[identifier]
    assert store.get('owner',identifier)['status']=='delivered'
    assert not list(store.artifact_root(identifier).glob('*'))
    assert all(method in ('GET','POST') and ('/ack' in path or method=='GET') for method,path in client.calls)
    api.close();store.close()


def test_missing_requested_movie_is_failure_not_completed_still(tmp_path,payload):
    store=JobStore(tmp_path/'state');compute=Compute();service=RenderService(store,compute)
    source=service.import_payload('owner',payload)['input_id']
    job=service.submit('owner',source,{'animation':'rock','width':320,'height':240},'movie')
    service._execute(store.claim())
    # Required expectations come from the admitted spec, even if an old backend
    # reports a PNG alone and omits its resolved-preset manifest.
    assert store.get('owner',job['job_id'])['status']=='failed'
    assert compute.calls==[job['job_id']]
    store.close()


def test_public_animations_rejected_before_dispatch(tmp_path,payload):
    import time
    store=JobStore(tmp_path,Policy(public=True,expires_at=time.time()+3600))
    compute=MovieCompute();service=RenderService(store,compute)
    source=service.import_payload('owner',payload)['input_id']
    with pytest.raises(ValueError,match='hosted animation'):
        service.submit('owner',source,{'animation':'rock'},'no-public-animation')
    assert compute.calls==[]
    store.close()
