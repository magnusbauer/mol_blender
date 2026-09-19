"""Public IDs use a fixed CIF source and normal admission, not fake attachments."""
import pytest
from test_jobs_api import payload
from test_d2_consumer import consumer, server
from test_d2_review import call, record


def test_reported_string_attachment_gives_actionable_error(consumer):
    response = call(server(consumer[0]), 'render_structure', {'file':'1QYS',
        'format':'pdb', 'request':{'request_key':'reported','preset':'neon_duplex_hero','animation':'360','gif':True}})
    result = record(response)
    assert result['code'] == 'attachment_required'
    assert 'render_public_structure' in result['error_message']
    assert consumer[0].store.recent('alice')['jobs'] == []


def test_public_id_uses_cif_and_recovery_precedes_download(consumer, monkeypatch):
    service, conversions, _ = consumer
    urls = []
    def download(url, **kwargs):
        urls.append(url)
        return b'public fixture'
    monkeypatch.setattr('blender_render.uploads.download_file', download)
    args = {'pdb_id':'1qys','request':{'request_key':'public','preset':'neon_duplex_hero'}}
    first = record(call(server(service), 'render_public_structure', args))
    assert first['job_id'] and first['status'] == 'queued'
    second = record(call(server(service), 'render_public_structure', args))
    assert second['job_id'] == first['job_id']
    assert urls == ['https://files.rcsb.org/download/1QYS.cif']
    service._execute(service.store.claim())
    assert len(conversions) == 1 and conversions[0][1] == '.cif'
    assert len(service.backend.calls) == 1


@pytest.mark.parametrize('identifier', ['https://evil.invalid', '../1QYS', '1QYS?x=y', '1QYS.pdb', ''])
def test_public_id_rejects_paths_before_download(consumer, identifier):
    service, conversions, downloads = consumer
    result = record(call(server(service), 'render_public_structure', {'pdb_id':identifier,
        'request':{'request_key':'invalid'}}))
    assert result['code'] == 'public_structure_invalid'
    assert not downloads and not conversions and not service.backend.calls
