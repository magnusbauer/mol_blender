"""Separate user requests render afresh; transport retries retain one paid job."""
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


@pytest.mark.parametrize('previous_state', ['queued', 'done', 'expired'])
def test_new_request_is_fresh_but_submission_retry_is_stable(consumer, previous_state):
    service, conversions, downloads = consumer
    initial = {'request_key': 'first', 'preset': 'neon_duplex_hero'}
    first = submit(service, initial)
    if previous_state != 'queued':
        service._execute(service.store.claim())
    if previous_state == 'expired':
        before = service.store.clock()
        service.store.clock = lambda: before + service.store.policy.ttl_seconds + 1
        service.store.cleanup()
    assert service.store.get('alice', first['job_id'])['status'] == previous_state
    # Transport retry retains its identity, including after the old result expires.
    before_downloads = len(downloads)
    assert submit(service, initial)['job_id'] == first['job_id']
    assert len(downloads) == before_downloads
    # A separate user request never returns an earlier operation's image/state.
    fresh = {**initial, 'request_key': 'second'}
    second = submit(service, fresh)
    assert second['job_id'] != first['job_id'] and second['status'] == 'queued'
    before_downloads = len(downloads)
    assert submit(service, fresh)['job_id'] == second['job_id']
    assert len(downloads) == before_downloads
    assert len(service.store.recent('alice')['jobs']) == 2
    # Same key with changed settings must not turn a retry into another job.
    assert submit(service, {**fresh, 'samples': 16}).get('job_id') is None
    assert len(service.store.recent('alice')['jobs']) == 2
