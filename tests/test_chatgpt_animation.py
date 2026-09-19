"""ChatGPT preset fidelity, one-job animations and direct GIF presentation."""
import json
import base64
import io
from dataclasses import replace
import pytest
from PIL import Image
from blender_render import consumer as contract
from blender_render.spec import resolve_options
from test_d2_consumer import consumer, server
from test_d2_review import call, record, submit
from test_jobs_api import payload


def test_preset_background_is_inherited_and_explicit_transparency_wins():
    request = contract.normalize_request({'request_key':'background','preset':'neon_duplex_hero'})
    style = resolve_options(contract.render_options(request), {})['style_doc']
    assert style['renderer']['transparent_background'] is False
    assert style['world']['top_color'] == '#F2A0EB'
    assert style['world']['bottom_color'] == '#77DFF5'
    request['hard_requirements']['transparent_background'] = True
    assert resolve_options(contract.render_options(request), {})['style_doc']['renderer']['transparent_background']


def test_motion_defaults_are_bounded_and_normalization_is_stable():
    value = {'request_key':'spin','preset':'neon_duplex_hero','animation':'360','gif':True}
    normalized = contract.normalize_request(value)
    assert contract.normalize_request(normalized) == normalized
    style = resolve_options(contract.render_options(normalized), {})['style_doc']
    assert style['output']['animation'] == {'mode':'360','duration':3,'fps':12,'gif':True}
    assert (style['output']['width'],style['output']['height'],style['output']['samples']) == (960,540,48)
    explicit = contract.normalize_request({**value,'width':320,'height':240,'samples':64,'duration':1,'fps':4})
    assert contract.normalize_request(explicit) == explicit
    assert resolve_options(contract.render_options(explicit), {})['style_doc']['output']['samples'] == 64


@pytest.mark.parametrize('values', [{'animation':'360','rock_angle':10}, {'gif':True},
    {'animation':'360','fps':60,'duration':15}, {'animation':'360','width':4096,'height':4096}])
def test_invalid_animation_never_admitted(consumer, values):
    service, conversions, downloads = consumer
    response = submit(service, {'request_key':'invalid','preset':'neon_duplex_hero',**values})
    assert response.get('job_id') is None
    assert not downloads and not conversions and not service.backend.calls


def test_unavailable_animation_stops_before_download(consumer):
    service, conversions, downloads = consumer
    service.store.policy = replace(service.store.policy, public=True)
    response = submit(service, {'request_key':'denied','preset':'neon_duplex_hero','animation':'360'})
    assert response['error_category'] == 'capability_unavailable'
    assert not downloads and not conversions


@pytest.mark.parametrize('gif_requested', [True, False])
def test_single_animation_job_returns_gif_not_frame_archive(consumer, gif_requested):
    service, conversions, downloads = consumer
    service.store.policy = replace(service.store.policy, allow_animation=True)
    original_render = service.backend.render
    buffer = io.BytesIO()
    Image.new('RGB',(4,3),'red').save(buffer,format='GIF',save_all=True,
        append_images=[Image.new('RGB',(4,3),'blue')],duration=100,loop=0)
    gif = buffer.getvalue()
    def render(*args):
        result = original_render(*args)
        if gif_requested:
            result['gif_base64'] = base64.b64encode(gif).decode()
        result.update(mp4_base64=base64.b64encode(b'fake backend mp4').decode(),
            frames_base64=base64.b64encode(b'fake backend archive').decode())
        return result
    service.backend.render = render
    job = submit(service, {'request_key':'movie','preset':'neon_duplex_hero','animation':'360','gif':gif_requested})
    assert job['status'] == 'queued'
    service._execute(service.store.claim())
    response = call(server(service), 'render_result', {'job_id':job['job_id']})
    assert record(response)['complete']
    assert {d['name'] for d in response.meta['downloads']} == ({'render.gif','render.mp4','render.png'} if gif_requested else {'render.mp4','render.png'})
    assert 'downloads' not in record(response)
    assert {f['name'] for f in response.meta['saveFiles']} == {f['name'] for f in response.meta['downloads']}
    assert 'saveFiles' not in record(response)
    assert response.meta['preview']['mimeType'] == ('image/gif' if gif_requested else 'image/png')
    if gif_requested:
        assert 'data' not in response.meta['preview']
        assert '/preview/' in response.meta['preview']['url']
        assert len(json.dumps(response.meta)) < 4096
        assert record(response)['delivery_mode'] == 'inline_viewer'
        from fastapi.testclient import TestClient
        from urllib.parse import urlsplit
        from blender_render.api import create_app
        from test_jobs_api import TOKEN
        with TestClient(create_app(service, token=TOKEN, signing_key='x' * 48,
                                  public_url='https://render.example.org')) as api:
            route = urlsplit(response.meta['preview']['url']).path
            for _ in range(2):
                fetched = api.get(route)
                assert fetched.status_code == 200 and fetched.content == gif
                assert fetched.headers['content-type'] == 'image/gif'
            mp4 = next(f for f in response.meta['saveFiles'] if f['name'] == 'render.mp4')
            for _ in range(2):
                video = api.get(urlsplit(mp4['url']).path)
                assert video.status_code == 200 and video.content == b'fake backend mp4'
                assert video.headers['content-type'] == 'video/mp4'
                assert video.headers['access-control-allow-origin'] == '*'
            assert api.get(route + 'tampered').status_code == 401
            assert (service.store.artifact_root(job['job_id']) / 'render.gif').exists()

    assert [item.name for item in response.content if item.type=='resource_link'] == ([] if gif_requested else ['render.mp4'])
    assert len(service.backend.calls)==len(conversions)==len(downloads)==1


def test_exact_preset_details(consumer):
    response = record(call(server(consumer[0]), 'preset_details', {'preset':'neon_duplex_hero'}))
    assert response['world']['top_color'] == '#F2A0EB'
    assert response['camera']['yaw_deg'] == 17.413858446592485


@pytest.mark.parametrize('owner,allowed,enabled', [('', '', False), ('123','',False),
    ('123','123,456',False), ('123','456',False), ('123','123',True)])
def test_hosted_animation_requires_exact_single_owner(owner, allowed, enabled):
    from blender_render.modal_boundaries import animation_enabled
    assert animation_enabled(personal=False, public=True, owner=owner, allowed_users=allowed) is enabled
    assert animation_enabled(personal=True, public=False, owner='', allowed_users='')
