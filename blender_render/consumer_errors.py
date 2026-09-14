"""Fixed public MCP errors. No exception text or submitted values are serialized."""
MESSAGES = {
    'attachment_unavailable': 'The attachment is unavailable. Attach it again for a new submission.',
    'format_unknown': 'Choose a supported molecular file format.',
    'invalid_structure': 'The molecular structure could not be validated or converted.',
    'selection_invalid': 'The structured render request is invalid.',
    'selection_incompatible': 'The selection does not satisfy the requirements or server policy.',
    'capability_unavailable': 'A required rendering capability is unavailable or disallowed.',
    'operation_conflict': 'The operation key is already bound to a different request.',
    'admission_refused': 'The service cannot admit this request under its current limits.',
    'compute_unavailable': 'Compute is unavailable. Inspect the existing operation before retrying.',
    'render_failed': 'Rendering failed. Inspect the existing operation.',
    'cancelled': 'The operation was cancelled.',
    'artifact_expired': 'The result artifacts have expired. Do not resubmit automatically.',
    'artifact_unavailable': 'A result artifact is unavailable. Recover a verified copy or stop.',
    'presentation_failed': 'Inline preview is unavailable. Use the delivered links or files.',
    'metadata_invalid': 'Stored consumer metadata requires operator inspection.',
    'restart_required': 'The existing operation requires a service restart.',
    'compatibility_blocked': 'The existing operation requires its compatible execution environment.',
    'execution_unknown': 'Execution is uncertain. Inspect this operation before taking action.',
    'recovery_required': 'The existing operation requires recovery inspection.',
    'transfer_pending': 'The existing render requires transfer recovery.',
    'not_ready': 'The existing render is not ready for delivery.',
    'operation_timeout': 'The bounded operation timed out. Reconnect to the existing job; do not resubmit.',
    'not_found': 'The requested owner-scoped resource was not found.',
    'unauthorized': 'Authentication is required.',
    'delivered': 'The result was delivered. Use the verified local copy.',
    'deleted': 'The result was deleted. Do not resubmit automatically.',
}
ALIASES = {
    'invalid_request': 'selection_invalid', 'selection_refused': 'selection_incompatible',
    'selection_policy_refused': 'selection_incompatible', 'color_follow_unavailable': 'selection_incompatible',
    'catalog_changed': 'selection_incompatible', 'resolution_invalid': 'selection_invalid',
    'idempotency_conflict': 'operation_conflict', 'snapshot_conflict': 'operation_conflict',
    'identity_conflict': 'operation_conflict', 'file_unavailable': 'attachment_unavailable',
    'expired': 'artifact_expired', 'checksum_mismatch': 'artifact_unavailable',
    'queue_full': 'admission_refused', 'daily_quota': 'admission_refused',
    'budget_exhausted': 'admission_refused', 'budget': 'admission_refused',
    'storage_full': 'admission_refused', 'too_large': 'admission_refused',
    'quota_exceeded': 'admission_refused', 'admission_closed': 'admission_refused',
    'credits_expired': 'admission_refused', 'state_unavailable': 'compute_unavailable',
    'runtime_conflict': 'compute_unavailable', 'http_error': 'compute_unavailable',
}


def category(code, default='selection_invalid'):
    return code if isinstance(code, str) and code in MESSAGES else ALIASES.get(code, default) if isinstance(code, str) else default


def error_record(code, *, default='selection_invalid'):
    safe = category(code, default)
    original = code if isinstance(code, str) and (code in MESSAGES or code in ALIASES) else safe
    action = {'operation_conflict': 'inspect_request_key', 'attachment_unavailable': 'reattach_file',
        'transfer_pending': 'recover_transfer', 'not_ready': 'render_status',
        'operation_timeout': 'render_status',
        'metadata_invalid': 'operator_inspection', 'presentation_failed': 'use_delivered_outputs'}.get(safe, 'inspect_operation_or_revise_request')
    return {'accepted': False, 'error_category': safe, 'code': original,
            'error_message': MESSAGES[safe], 'next_action': action}


def from_exception(exc, *, default='selection_invalid'):
    # Tool managers may wrap application exceptions. Inspect types/codes only.
    seen = set()
    fallback = default
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        code = getattr(exc, 'code', None)
        if code == 'invalid_structure':
            # Legacy conversion wrappers must not erase a typed cause.
            fallback = 'invalid_structure'
        elif isinstance(code, str) and (code in MESSAGES or code in ALIASES) and code != 'invalid_request':
            return error_record(code, default=default)
        name = type(exc).__name__
        if name == 'ClientError' and code == 'client_error':
            return error_record('artifact_unavailable' if default == 'artifact_unavailable' else 'compute_unavailable')
        mapped = {'RestartRequired': 'restart_required', 'EngineMismatch': 'compatibility_blocked', 'RuntimeUnavailable': 'compute_unavailable',
            'ExecutionUnknown': 'execution_unknown', 'StoredSnapshotIntegrityError': 'recovery_required',
            'MetadataError': 'metadata_invalid', 'FileNotFoundError': 'attachment_unavailable'}.get(name)
        if mapped:
            return error_record(mapped)
        exc = exc.__cause__
    return error_record(fallback)
