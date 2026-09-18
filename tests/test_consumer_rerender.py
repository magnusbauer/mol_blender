"""Explicit repeats bypass content reuse, while retries retain one paid job."""
import pytest
from blender_render import consumer as contract
from blender_render.jobs import JobError
from test_d2_consumer import consumer
from test_d2_review import submit
from test_jobs_api import payload


def test_default_identity_survives_new_repeat_control():
    request = {'request_key': 'original', 'preset': 'neon_duplex_hero'}
    original = contract.normalize_request(request)
    assert 'rerender' not in original
    assert contract.normalize_request({**request, 'rerender': False}) == original
    explicit = contract.normalize_request({**request, 'rerender': True})
    assert explicit['rerender'] is True
    assert contract.identity(explicit) != contract.identity(original)
    assert 'rerender' not in contract.render_options(explicit)


@pytest.mark.parametrize('bad', [1, 'true', None])
def test_repeat_flag_is_strict(bad):
    with pytest.raises(JobError):
        contract.normalize_request({'request_key': 'invalid', 'rerender': bad})


def test_expired_result_requires_explicit_repeat_and_retry_does_not_repeat(consumer):
    service, conversions, downloads = consumer
    initial = {'request_key': 'first', 'preset': 'neon_duplex_hero'}
    first = submit(service, initial)
    service._execute(service.store.claim())
    before = service.store.clock()
    service.store.clock = lambda: before + service.store.policy.ttl_seconds + 1
    service.store.cleanup()
    assert service.store.get('alice', first['job_id'])['status'] == 'expired'
    # Even a different key is not implicit permission to replace an expired job.
    reused = submit(service, {**initial, 'request_key': 'accidental-retry'})
    assert reused['job_id'] == first['job_id'] and reused['status'] == 'expired'
    # The original key cannot change meaning by toggling the repeat flag.
    assert submit(service, {**initial, 'rerender': True}).get('job_id') is None
    repeat = {**initial, 'request_key': 'explicit-second', 'rerender': True}
    second = submit(service, repeat)
    assert second['job_id'] != first['job_id'] and second['status'] == 'queued'
    downloads_before = len(downloads)
    assert submit(service, repeat)['job_id'] == second['job_id']
    assert len(downloads) == downloads_before  # Recovery precedes attachment I/O.
    assert len(service.backend.calls) == 1
    service._execute(service.store.claim())
    assert service.store.get('alice', second['job_id'])['status'] == 'done'
    assert len(service.backend.calls) == 2
    third = submit(service, {**repeat, 'request_key': 'explicit-third'})
    assert third['job_id'] not in {first['job_id'], second['job_id']}
    assert submit(service, {**repeat, 'request_key': 'explicit-third'})['job_id'] == third['job_id']
