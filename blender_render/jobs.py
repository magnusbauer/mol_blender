"""Durable FIFO jobs, ownership, quotas, and conservative compute reservations.

Exactly one service process owns this database. On Modal the database lives on
its own Volume; ``sync`` commits the Volume before any paid work is dispatched.
Never mount this database in render workers or run overlapping deployments.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import artifacts as artifact_lifecycle
from .compatibility import sanitize, message
from . import consumer_records, consumer_errors


class JobError(ValueError):
    def __init__(self, message: str, code: str = "invalid_request", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Policy:
    public: bool = False
    budget_usd: float = 450.0
    daily_jobs: int = 10
    quota_exempt_owners: tuple[str, ...] = ()
    max_job_seconds: int = 600
    startup_seconds: int = 180
    # L4 + four CPU cores + 8 GiB, rounded up with a safety margin. This is
    # deliberately a conservative reservation, not a claim of billed cost.
    usd_per_second: float = 0.0004
    expires_at: float = 1806451200.0  # 2027-03-31 00:00 UTC, conservative grant expiry
    ttl_seconds: int = 900
    input_ttl_seconds: int = 300
    max_upload_bytes: int = 50 * 1024 * 1024
    max_atoms: int = 100000
    max_storage_bytes: int = 10 * 1024**3
    max_queue: int = 100

    def reserve(self) -> int:
        return math.ceil((self.max_job_seconds + self.startup_seconds) * self.usd_per_second * 1_000_000) if self.public else 0


class JobStore:
    STAGES = ('validation', 'conversion', 'waiting_for_compute', 'scene_build',
              'rendering', 'result_ready', 'delivery', 'failed', 'cancelled')

    def __init__(self, root: Path, policy: Policy | None = None, *, data_root: Path | None = None, sync: Callable[[], None] | None = None, clock=time.time):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        transient = Path(data_root).expanduser().resolve() if data_root else self.root
        self.inputs = transient / "inputs"
        self.artifacts = transient / "artifacts"
        self.inputs.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.policy = policy or Policy()
        self.session_id = os.environ.get("BLENDER_RENDER_SESSION_ID") or secrets.token_hex(12)
        self.clock = clock
        self._queue_clocks = {}
        self.sync = sync or (lambda: None)
        self.lock = threading.RLock()
        self.integrity_lock = self.lock  # Bound to the process seal by its controller.
        self.healthy = True
        self.db = sqlite3.connect(str(self.root / "jobs.sqlite3"), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inputs (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL,
          expires REAL NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, input_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, options TEXT NOT NULL,
          state TEXT NOT NULL, submitted REAL NOT NULL, started REAL, finished REAL,
          reserved INTEGER NOT NULL, charged INTEGER NOT NULL DEFAULT 0,
          gpu_seconds REAL NOT NULL DEFAULT 0, remote_id TEXT,
          cancel_requested INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT,
          UNIQUE(owner, idempotency_key));
        CREATE INDEX IF NOT EXISTS job_queue ON jobs(state, submitted);
        CREATE TABLE IF NOT EXISTS operations (
          id INTEGER PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL,
          amount INTEGER NOT NULL, note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS delivery_receipts (
          owner TEXT NOT NULL, job_id TEXT NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL,
          PRIMARY KEY(owner,job_id,name));
        CREATE TABLE IF NOT EXISTS artifact_roots (path TEXT PRIMARY KEY);
        """)
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}
        # One additive transaction; NULL resume_phase identifies genuinely older
        # rows rather than asserting they never crossed an execution boundary.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for column, definition in (('blocked_condition', 'TEXT'), ('resume_phase', 'TEXT'),
                                       ('compatibility_diagnostic', 'TEXT'),
                                       ('dispatch_fenced', 'INTEGER NOT NULL DEFAULT 0'),
                                       ('child_timing_import', 'TEXT'), ('timing_conflict', 'INTEGER NOT NULL DEFAULT 0'),
                                       ('recovery_ready', 'INTEGER NOT NULL DEFAULT 0'),
                                       ('conversion_reserved', 'INTEGER NOT NULL DEFAULT 0')):
                if column not in columns:
                    self.db.execute('ALTER TABLE jobs ADD COLUMN ' + column + ' ' + definition)
            if 'consumer_metadata' not in columns:
                self.db.execute('ALTER TABLE jobs ADD COLUMN consumer_metadata TEXT')
            self.db.execute('CREATE INDEX IF NOT EXISTS runnable_jobs ON jobs(state,blocked_condition,submitted)')
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        self.process_blocked = lambda: False
        for column in ('expected_artifacts', 'artifact_states'):
            if column not in columns:
                self.db.execute('ALTER TABLE jobs ADD COLUMN ' + column + ' TEXT')
        for column in ('session_id','submitted_by'):
            if column not in columns:
                self.db.execute('ALTER TABLE jobs ADD COLUMN ' + column + ' TEXT')
        if 'rendered' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN rendered INTEGER NOT NULL DEFAULT 0')
            self.db.execute("UPDATE jobs SET rendered=1 WHERE state='done'")
        if 'client_request_hash' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN client_request_hash TEXT')
        for column in ('render_fingerprint', 'source_key'):
            if column not in columns:
                self.db.execute('ALTER TABLE jobs ADD COLUMN ' + column + ' TEXT')
        for column, definition in (('stage', "TEXT NOT NULL DEFAULT 'validation'"),
                                   ('stage_history', "TEXT NOT NULL DEFAULT '[]'"),
                                   ('operation_key', 'TEXT'), ('input_sha256', 'TEXT'), ('execution', 'TEXT'),
                                   ('remote_phase', 'TEXT'), ('remote_completed_at', 'REAL'),
                                   ('timings', "TEXT NOT NULL DEFAULT '[]'"), ('transfer_attempt', 'INTEGER NOT NULL DEFAULT 0')):
            if column not in columns:
                self.db.execute('ALTER TABLE jobs ADD COLUMN ' + column + ' ' + definition)
        self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS logical_operations ON jobs(owner,operation_key)')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS operation_keys (
          owner TEXT NOT NULL, key TEXT NOT NULL, job_id TEXT NOT NULL,
          PRIMARY KEY(owner,key));
        INSERT OR IGNORE INTO operation_keys(owner,key,job_id)
          SELECT owner,operation_key,id FROM jobs WHERE operation_key IS NOT NULL;
        ''')
        alias_columns = {row[1] for row in self.db.execute('PRAGMA table_info(operation_keys)')}
        for column in ('input_id', 'input_sha256', 'client_hash', 'source_key'):
            if column not in alias_columns:
                self.db.execute('ALTER TABLE operation_keys ADD COLUMN ' + column + ' TEXT')
        self.db.execute('''UPDATE operation_keys SET
          input_id=(SELECT input_id FROM jobs WHERE id=job_id),
          input_sha256=(SELECT input_sha256 FROM jobs WHERE id=job_id),
          client_hash=(SELECT client_request_hash FROM jobs WHERE id=job_id),
          source_key=(SELECT source_key FROM jobs WHERE id=job_id)
          WHERE input_id IS NULL''')
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS submission_keys (
          owner TEXT NOT NULL, key TEXT NOT NULL, input_id TEXT NOT NULL,
          client_hash TEXT, request_hash TEXT NOT NULL, job_id TEXT NOT NULL,
          PRIMARY KEY(owner,key));
        INSERT OR IGNORE INTO submission_keys
          SELECT owner,idempotency_key,input_id,client_request_hash,request_hash,id FROM jobs;
        CREATE INDEX IF NOT EXISTS render_retries ON jobs(owner,render_fingerprint);
        CREATE INDEX IF NOT EXISTS file_retries ON jobs(owner,source_key,client_request_hash);
        """)
        if 'result_expires' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN result_expires REAL')
            # Preserve existing download deadlines; new jobs use the short
            # delivery window. Upgrades do not purge completed renders.
            self.db.execute("UPDATE jobs SET result_expires=finished+86400 WHERE result IS NOT NULL")
        input_columns = {row[1] for row in self.db.execute('PRAGMA table_info(inputs)')}
        for column in ('content_sha256', 'source_key'):
            if column not in input_columns:
                self.db.execute('ALTER TABLE inputs ADD COLUMN ' + column + ' TEXT')
        with self.transaction() as db:
            # Never replenish a lifetime budget when a deployment restarts.
            db.execute("INSERT OR IGNORE INTO settings VALUES ('budget_micro', ?)", (str(math.floor(self.policy.budget_usd * 1_000_000)),))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('policy', ?)", (json.dumps(asdict(self.policy)),))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('admission', 'open')")
            for row in db.execute("SELECT id FROM inputs WHERE content_sha256 IS NULL").fetchall():
                path = self.input_path(row['id'])
                if path.is_file():
                    digest = self._content_digest(json.loads(path.read_text()))
                    db.execute("UPDATE inputs SET content_sha256=? WHERE id=?", (digest, row['id']))
            artifact_lifecycle.initialize(self, db)
            artifact_lifecycle.sweep(self, db)
        artifact_lifecycle.purge(self.db)

    @contextmanager
    def transaction(self):
        # Serialize durable admission with integrity detection. Detection either
        # precedes the reservation (and rejects it) or follows its commit (and
        # preserves the already-admitted operation). Operator drain is separate.
        with self.lock, self.integrity_lock:
            if not self.healthy:
                raise JobError("Persistent state is unavailable; compute admission is closed.", "state_unavailable", 503)
            changes = self.db.total_changes
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
            try:
                if self.db.total_changes != changes:
                    self.sync()
            except BaseException:
                self.healthy = False
                raise

    def _usage(self, db) -> tuple[int, int, int]:
        cap = min(int(db.execute("SELECT value FROM settings WHERE key='budget_micro'").fetchone()[0]), math.floor(self.policy.budget_usd * 1_000_000))
        spent, reserved = db.execute("SELECT COALESCE(SUM(charged),0), COALESCE(SUM(CASE WHEN state IN ('queued','running','transfer_pending') THEN reserved ELSE 0 END),0) FROM jobs").fetchone()
        spent += db.execute("SELECT COALESCE(SUM(amount),0) FROM operations").fetchone()[0]
        return cap, int(spent), int(reserved)

    def storage_bytes(self, db):
        inputs = db.execute('SELECT COALESCE(SUM(size),0) FROM inputs').fetchone()[0]
        artifacts = db.execute("SELECT COALESCE(SUM(json_extract(result,'$.bytes')),0) FROM jobs WHERE result IS NOT NULL").fetchone()[0]
        snapshots = db.execute("SELECT COALESCE(SUM(length(CAST(execution AS BLOB))),0)+COALESCE(SUM(length(CAST(options AS BLOB))),0) FROM jobs").fetchone()[0]
        metadata = db.execute('SELECT COALESCE(SUM(length(CAST(consumer_metadata AS BLOB))),0) FROM jobs').fetchone()[0]
        return inputs + artifacts + snapshots + metadata

    def _check_storage(self, db, extra=0):
        if self.policy.public and self.storage_bytes(db) + extra > self.policy.max_storage_bytes:
            raise JobError('Upload/snapshot storage capacity is temporarily full', 'storage_full', 503)

    def _admit(self, db, amount: int, *, dispatch: bool = False):
        if self.process_blocked():
            from .engine import RestartRequired
            raise RestartRequired('restart_required: execution admission is paused')
        if not dispatch and db.execute("SELECT value FROM settings WHERE key='admission'").fetchone()[0] != "open":
            raise JobError("Service is draining. Please retry later or use your own compute.", "admission_closed", 503)
        if self.policy.public:
            if self.clock() >= self.policy.expires_at:
                raise JobError("The free compute grant has expired. Connect your own compute.", "credits_expired", 402)
            cap, spent, reserved = self._usage(db)
            if spent + reserved + amount > cap:
                raise JobError("Free compute credits are exhausted. Connect your own compute.", "budget_exhausted", 402)

    def quota(self, owner: str) -> dict:
        now = self.clock()
        day = math.floor(now / 86400) * 86400
        with self.lock:
            cap, spent, reserved = self._usage(self.db)
            count = self.db.execute("SELECT COUNT(*) FROM jobs WHERE owner=? AND submitted>=? AND (rendered=1 OR state IN ('queued','running','transfer_pending'))", (owner, day)).fetchone()[0]
            retained, next_expiry = self.db.execute("SELECT COUNT(*),MIN(expires) FROM inputs WHERE owner=? AND expires>?", (owner, now)).fetchone()
        limited = self.policy.public and owner not in self.policy.quota_exempt_owners
        return {"public": self.policy.public, "budget_remaining_usd": max(0, cap - spent - reserved) / 1_000_000 if self.policy.public else None,
                "estimated_spent_usd": spent / 1_000_000, "reserved_usd": reserved / 1_000_000,
                "renders_remaining_today": max(0, self.policy.daily_jobs - count) if limited else None,
                "daily_render_limit": self.policy.daily_jobs if limited else None,
                "renders_completed_or_reserved_today": count,
                "testing_quota_exempt": owner in self.policy.quota_exempt_owners,
                "gpu_seconds_remaining_today": None,
                "pending_uploads": retained,
                "retained_structure_limit": None,
                "upload_slots_remaining": None,
                "conversions_remaining_today": None,
                "next_structure_expiry_at": next_expiry,
                "daily_quota_resets_at": day + 86400 if self.policy.public else None,
                "credits_expire_at": self.policy.expires_at if self.policy.public else None,
                "accounting": "conservative estimates; Modal workspace cap is the final billing limit"}

    def charge_operation(self, owner: str, amount_usd: float, note: str):
        if not math.isfinite(amount_usd) or amount_usd < 0:
            raise ValueError("Charges must be nonnegative and finite")
        amount = math.ceil(amount_usd * 1_000_000) if self.policy.public else 0
        with self.transaction() as db:
            self._admit(db, amount)
            db.execute("INSERT INTO operations(owner,created,amount,note) VALUES (?,?,?,?)", (owner, self.clock(), amount, note))

    def reserve_conversion(self, owner: str, *, job_id=None):
        """Reserve sandbox cost without imposing an upload-count quota."""
        with self.transaction() as db:
            amount = 10000 if self.policy.public else 0
            if job_id is not None and not db.execute(
                    "SELECT 1 FROM jobs WHERE id=? AND owner=? AND state='running'", (job_id, owner)).fetchone():
                raise JobError('Conversion requires the admitted running job')
            if job_id and db.execute('SELECT conversion_reserved FROM jobs WHERE id=?', (job_id,)).fetchone()[0]:
                return
            self._admit(db, amount, dispatch=job_id is not None)
            db.execute("INSERT INTO operations(owner,created,amount,note) VALUES (?,?,?,'conversion')", (owner, self.clock(), amount))
            if job_id:
                db.execute('UPDATE jobs SET conversion_reserved=1 WHERE id=?', (job_id,))

    def input_path(self, identifier):
        path = self.inputs / (identifier + '.json')
        legacy = self.root / 'inputs' / (identifier + '.json')
        return path if path.exists() or not legacy.exists() else legacy

    def artifact_root(self, identifier):
        path = self.artifacts / identifier
        legacy = self.root / 'artifacts' / identifier
        return path if path.exists() or not legacy.exists() else legacy
    @staticmethod
    def _content_digest(payload: dict) -> str:
        # Export timestamps and run IDs do not change the structure or pose.
        # Every other field, including camera, colors and DSS, remains hashed.
        content = {k: v for k, v in payload.items() if k not in {'generated_at_utc', 'run_id'}}
        return hashlib.sha256(json.dumps(content, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def _input_info(self, row, *, reused: bool = False) -> dict:
        path = self.input_path(row['id'])
        payload = json.loads(path.read_text()) if path.is_file() else {}
        return {"input_id": row['id'], "name": row['name'], "created_at": row['created'],
                "expires_at": row['expires'], "sha256": row['sha256'], "size_bytes": row['size'],
                "summary": payload.get('structure_summary', {}), "reused": reused}

    def find_source(self, owner: str, source_key: str) -> dict | None:
        """Only server-computed file digests can bypass another conversion."""
        with self.lock:
            row = self.db.execute("SELECT * FROM inputs WHERE owner=? AND source_key=? AND expires>? ORDER BY created,id LIMIT 1", (owner, source_key, self.clock())).fetchone()
            return self._input_info(row, reused=True) if row else None

    def list_inputs(self, owner: str) -> dict:
        with self.lock:
            rows = self.db.execute("SELECT * FROM inputs WHERE owner=? ORDER BY created,id", (owner,)).fetchall()
            items = []
            for row in rows:
                item = self._input_info(row)
                item.pop('reused')
                item['reusable'] = row['expires'] > self.clock()
                item['jobs'] = [dict(j) for j in self.db.execute("SELECT id AS job_id,state AS status FROM jobs WHERE input_id=? AND owner=? ORDER BY submitted DESC", (row['id'], owner))]
                item['in_use'] = any(j['status'] in {'queued', 'running'} for j in item['jobs'])
                items.append(item)
            return {"structures": items, "quota": self.quota(owner)}

    def delete_input(self, owner: str, identifier: str) -> dict:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM inputs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                # The response does not disclose another user's input IDs.
                return {"input_id": identifier, "deleted": False}
            if db.execute("SELECT 1 FROM jobs WHERE input_id=? AND state IN ('queued','running','transfer_pending')", (identifier,)).fetchone():
                raise JobError("This structure is used by an active render. Wait for completion or cancel that job first.", "input_in_use", 409)
            self.input_path(identifier).unlink(missing_ok=True)
            db.execute("DELETE FROM inputs WHERE id=? AND owner=?", (identifier, owner))
        return {"input_id": identifier, "deleted": True}

    def add_input(self, owner: str, payload: dict, name: str = "structure.cif", *, source_key: str | None = None, raw_file: bool = False) -> dict:
        data = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        limit = self.policy.max_upload_bytes * 4 // 3 + 65536 if raw_file else self.policy.max_upload_bytes
        if len(data) > limit:
            raise JobError("Structure exceeds the upload size limit.", "too_large", 413)
        now = self.clock()
        identifier = secrets.token_hex(16)
        path = self.inputs / (identifier + ".json")
        digest = self._content_digest(payload)
        with self.transaction() as db:
            existing = db.execute("SELECT * FROM inputs WHERE owner=? AND content_sha256=? AND expires>? ORDER BY created,id LIMIT 1", (owner, digest, now)).fetchone()
            if existing:
                if source_key:
                    db.execute("UPDATE inputs SET source_key=? WHERE id=?", (source_key, existing['id']))
                return self._input_info(existing, reused=True)
            self._admit(db, 0)
            self._check_storage(db, len(data))
            path.write_bytes(data)
            path.chmod(0o600)
            db.execute("INSERT INTO inputs(id,owner,created,expires,size,sha256,name,content_sha256,source_key) VALUES (?,?,?,?,?,?,?,?,?)", (identifier, owner, now, now + self.policy.input_ttl_seconds, len(data), hashlib.sha256(data).hexdigest(), Path(name).name[:160], digest, source_key))
            return self._input_info(db.execute("SELECT * FROM inputs WHERE id=?", (identifier,)).fetchone())

    def input(self, owner: str, identifier: str) -> dict:
        if not isinstance(identifier, str):
            raise JobError("input_id must be a string")
        with self.lock:
            row = self.db.execute("SELECT * FROM inputs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise JobError("Temporary structure is unavailable; upload it again.", "not_found", 404)
            active = self.db.execute("SELECT 1 FROM jobs WHERE input_id=? AND state IN ('queued','running','transfer_pending')", (identifier,)).fetchone()
            if row["expires"] <= self.clock() and not active:
                raise JobError("Temporary structure has expired; upload it again.", "expired", 410)
            path = self.input_path(row['id'])
            if not path.is_file():
                raise JobError("Temporary structure was lost on restart; upload it again.", "expired", 410)
            return json.loads(path.read_text())

    def _remember_submission(self, db, owner, input_id, key, client_hash, request_hash, job_id):
        db.execute("INSERT OR IGNORE INTO submission_keys VALUES (?,?,?,?,?,?)",
                   (owner, key, input_id, client_hash, request_hash, job_id))

    def _reused_job(self, row):
        return {**self._public_job(row), 'reused': True}

    def _bind_operation(self, db, owner, key, identifier, *, input_id=None, client_hash=None, source_key=None):
        if key is None:
            return
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise JobError('operation_key must contain 1 to 128 characters')
        row = db.execute('SELECT job_id FROM operation_keys WHERE owner=? AND key=?', (owner, key)).fetchone()
        if row and row['job_id'] != identifier:
            raise JobError('Operation key already identifies another job', 'operation_conflict', 409)
        job = db.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (identifier, owner)).fetchone()
        accepted_input = input_id or job['input_id']
        source = db.execute('SELECT content_sha256 FROM inputs WHERE id=? AND owner=?', (accepted_input, owner)).fetchone()
        digest = source['content_sha256'] if source else job['input_sha256']
        db.execute('INSERT OR IGNORE INTO operation_keys(owner,key,job_id,input_id,input_sha256,client_hash,source_key) VALUES (?,?,?,?,?,?,?)',
                   (owner, key, identifier, accepted_input, digest,
                    client_hash if client_hash is not None else job['client_request_hash'],
                    source_key if source_key is not None else job['source_key']))

    def remember_operation(self, owner, key, job, **identity):
        with self.transaction() as db:
            self._bind_operation(db, owner, key, job['job_id'], **identity)
        return job

    def file_replay(self, owner, source_key, client_hash):
        """Recover an operation, never permission to replace an expired render."""
        with self.lock:
            row = self.db.execute("""SELECT * FROM jobs WHERE owner=? AND source_key=?
                AND client_request_hash=?
                ORDER BY submitted,id LIMIT 1""",
                (owner, source_key, client_hash)).fetchone()
            return self._reused_job(row) if row else None

    def operation_replay(self, owner, operation_key, client_hash, *, input_id=None, source_key=None):
        if operation_key is None:
            return None
        if not isinstance(operation_key, str) or not 1 <= len(operation_key) <= 128:
            raise JobError('operation_key must contain 1 to 128 characters')
        with self.lock:
            row = self.db.execute('''SELECT j.*, k.input_id AS alias_input_id,
                k.input_sha256 AS alias_input_sha256, k.client_hash AS alias_client_hash,
                k.source_key AS alias_source_key FROM jobs j JOIN operation_keys k ON j.id=k.job_id
                WHERE k.owner=? AND k.key=?''', (owner, operation_key)).fetchone()
            if row:
                same_input = input_id is None or row['alias_input_id'] == input_id
                if not same_input:
                    source = self.db.execute('SELECT content_sha256 FROM inputs WHERE id=? AND owner=?', (input_id, owner)).fetchone()
                    same_input = bool(source and source['content_sha256'] == row['alias_input_sha256'])
                if (row['alias_client_hash'] != client_hash
                        or not same_input
                        or source_key is not None and row['alias_source_key'] != source_key):
                    raise JobError('Operation already exists with a different request; recover it instead of creating a hidden preview.', 'operation_conflict', 409)
                return self._reused_job(row)

    def submission_replay(self, owner, input_id, key, client_hash, *, rerender=False, legacy_hash=None):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise JobError("Provide an idempotency key of 1 to 128 characters.")
        if not isinstance(input_id, str):
            raise JobError("input_id must be a string")
        with self.transaction() as db:
            alias = db.execute("SELECT * FROM submission_keys WHERE owner=? AND key=?", (owner, key)).fetchone()
            if alias and alias['client_hash']:
                if alias['input_id'] != input_id or alias['client_hash'] not in {client_hash, legacy_hash}:
                    raise JobError("Idempotency key was already used for a different request.", "idempotency_conflict", 409)
                return self._reused_job(db.execute('SELECT * FROM jobs WHERE id=?', (alias['job_id'],)).fetchone())
            # An agent may lose its retry key after a tool approval or handoff.
            # The input ID and request still identify the same operation, even
            # after privacy cleanup has deleted the coordinates and options.
            if not alias and not rerender:
                row = db.execute("SELECT j.* FROM jobs j JOIN submission_keys s ON j.id=s.job_id WHERE s.owner=? AND s.input_id=? AND s.client_hash=? ORDER BY j.submitted,j.id LIMIT 1", (owner, input_id, client_hash)).fetchone()
                if row:
                    self._remember_submission(db, owner, input_id, key, client_hash, row['request_hash'], row['id'])
                    return self._reused_job(row)
            return None

    def submit(self, owner: str, input_id: str, options: dict, idempotency_key: str, *, client_hash: str | None = None, rerender: bool = False, operation_key: str | None = None, consumer_metadata=None) -> dict:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise JobError("Provide an idempotency key of 1 to 128 characters.")
        encoded = json.dumps(options, sort_keys=True, allow_nan=False, separators=(",", ":"))
        if consumer_metadata is not None:
            consumer_metadata = consumer_records.validate_metadata(consumer_metadata)
        intent = consumer_metadata['request_fingerprint'] if consumer_metadata else ''
        request_hash = hashlib.sha256((input_id + encoded + intent).encode()).hexdigest()
        now = self.clock()
        identifier = secrets.token_hex(16)
        with self.transaction() as db:
            replay = self.operation_replay(owner, operation_key, client_hash, input_id=input_id)
            if replay:
                return replay
            alias = db.execute("SELECT * FROM submission_keys WHERE owner=? AND key=?", (owner, idempotency_key)).fetchone()
            if alias:
                if alias["request_hash"] != request_hash:
                    raise JobError("Idempotency key was already used for a different request.", "idempotency_conflict", 409)
                self._bind_operation(db, owner, operation_key, alias['job_id'], input_id=input_id, client_hash=client_hash)
                return self._reused_job(db.execute('SELECT * FROM jobs WHERE id=?', (alias['job_id'],)).fetchone())
            self.input(owner, input_id)
            source = db.execute('SELECT * FROM inputs WHERE id=? AND owner=?', (input_id, owner)).fetchone()
            fingerprint = hashlib.sha256((source['content_sha256'] + encoded + intent).encode()).hexdigest()
            if not rerender:
                existing = db.execute("""SELECT * FROM jobs WHERE owner=? AND render_fingerprint=?
                    ORDER BY submitted,id LIMIT 1""", (owner, fingerprint)).fetchone()
                if existing:
                    self._bind_operation(db, owner, operation_key, existing['id'], input_id=input_id, client_hash=client_hash)
                    self._remember_submission(db, owner, input_id, idempotency_key, client_hash, request_hash, existing['id'])
                    if existing['input_id'] != input_id and not db.execute("SELECT 1 FROM jobs WHERE input_id=? AND state IN ('queued','running','transfer_pending')", (input_id,)).fetchone():
                        self.input_path(input_id).unlink(missing_ok=True)
                        db.execute('DELETE FROM inputs WHERE id=?', (input_id,))
                    return self._reused_job(existing)
            reservation = self.policy.reserve()
            self._admit(db, reservation)
            if db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','transfer_pending')").fetchone()[0] >= self.policy.max_queue:
                raise JobError("Render queue is full; try again later.", "queue_full", 429)
            if self.policy.public:
                day = math.floor(now / 86400) * 86400
                count = db.execute("SELECT COUNT(*) FROM jobs WHERE owner=? AND submitted>=? AND (rendered=1 OR state IN ('queued','running','transfer_pending'))", (owner, day)).fetchone()[0]
                if owner not in self.policy.quota_exempt_owners and count >= self.policy.daily_jobs:
                    raise JobError("Your daily free render quota is used up.", "daily_quota", 429)
            db.execute("INSERT INTO jobs(id,owner,input_id,idempotency_key,request_hash,options,state,submitted,reserved) VALUES (?,?,?,?,?,?,'queued',?,?)", (identifier, owner, input_id, idempotency_key, request_hash, encoded, now, reservation))
            db.execute("UPDATE jobs SET resume_phase='admitted' WHERE id=?", (identifier,))
            if consumer_metadata is not None:
                db.execute('UPDATE jobs SET consumer_metadata=? WHERE id=?',
                           (consumer_records.dumps(consumer_metadata), identifier))
            db.execute("UPDATE jobs SET session_id=?,submitted_by=?,client_request_hash=?,render_fingerprint=?,source_key=? WHERE id=?", (self.session_id,owner,client_hash,fingerprint,source['source_key'],identifier))
            self._remember_submission(db, owner, input_id, idempotency_key, client_hash, request_hash, identifier)
            db.execute('UPDATE jobs SET operation_key=?,stage_history=?,input_sha256=? WHERE id=?',
                       (operation_key, json.dumps([{'stage': 'validation', 'at': now}]), source['content_sha256'], identifier))
            self._bind_operation(db, owner, operation_key, identifier)
            if 'render_spec' in options:
                from .execution import freeze_execution
                snapshot = freeze_execution(self.input(owner, input_id), options)
                db.execute('UPDATE jobs SET execution=? WHERE id=?', (json.dumps(snapshot, allow_nan=False), identifier))
            self._check_storage(db)
            self._queue_clocks[identifier] = time.monotonic()
        return self.get(owner, identifier)

    def _stage(self, db, identifier, stage):
        if stage not in self.STAGES:
            raise ValueError('Unsupported operation stage: ' + stage)
        row = db.execute('SELECT stage,stage_history FROM jobs WHERE id=?', (identifier,)).fetchone()
        if row and row['stage'] != stage:
            if self.STAGES.index(stage) < self.STAGES.index(row['stage']):
                return
            history = json.loads(row['stage_history'])
            history.append({'stage': stage, 'at': self.clock()})
            db.execute('UPDATE jobs SET stage=?,stage_history=? WHERE id=?', (stage, json.dumps(history), identifier))

    def stage(self, identifier, stage):
        with self.transaction() as db:
            row = db.execute('SELECT state FROM jobs WHERE id=?', (identifier,)).fetchone()
            if row and row['state'] in ('queued', 'running', 'transfer_pending'):
                self._stage(db, identifier, stage)

    def bind_execution(self, identifier, snapshot):
        with self.transaction() as db:
            row = db.execute('SELECT execution FROM jobs WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise JobError('Operation not found', 'not_found', 404)
            if row['execution'] and json.loads(row['execution']) != snapshot:
                raise JobError('Execution snapshot is immutable', 'snapshot_conflict', 409)
            db.execute('UPDATE jobs SET execution=? WHERE id=?', (json.dumps(snapshot, allow_nan=False), identifier))
            self._check_storage(db)

    def bind_consumer_decision(self, identifier, metadata):
        """Only the deferred preselection -> exact validation transition is mutable."""
        from .consumer_records import validate_metadata, validate_transition
        metadata = validate_metadata(metadata)
        with self.transaction() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
            previous = consumer_records.loads(row['consumer_metadata']) if row else None
            if (not previous or previous['selection_phase'] != 'preselection'
                    or row['state'] != 'running' or row['execution']
                    or metadata['selection_phase'] != 'refused'):
                raise JobError('Consumer decision is immutable', 'snapshot_conflict', 409)
            validate_transition(previous, metadata)
            db.execute('UPDATE jobs SET consumer_metadata=? WHERE id=?',
                       (consumer_records.dumps(metadata), identifier))
            self._check_storage(db)

    def bind_spec(self, identifier, request, payload=None, *, consumer_metadata=None):
        """Bind the one resolved snapshot after deferred file conversion."""
        with self.transaction() as db:
            row = db.execute('SELECT options,execution,state,consumer_metadata FROM jobs WHERE id=?', (identifier,)).fetchone()
            if not row or row['state'] != 'running' or 'prepare_file' not in json.loads(row['options']):
                raise JobError('Only a running deferred conversion can bind execution')
            if row and row['execution']:
                raise JobError('Execution snapshot is immutable', 'snapshot_conflict', 409)
            if payload is None:
                raise JobError('Binding requires both immutable snapshots')
            if row['consumer_metadata'] is not None and consumer_metadata is None:
                raise JobError('Binding requires the exact-context consumer decision')
            from .execution import freeze_execution
            snapshot = freeze_execution(payload, request)
            if consumer_metadata is not None:
                from .consumer_records import validate_metadata, validate_transition
                consumer_metadata = validate_metadata(consumer_metadata)
                old = db.execute('SELECT consumer_metadata FROM jobs WHERE id=?', (identifier,)).fetchone()[0]
                if (not old or consumer_records.loads(old)['selection_phase'] != 'preselection'
                        or consumer_metadata['selection_phase'] != 'bound'):
                    raise JobError('Consumer decision is immutable', 'snapshot_conflict', 409)
                validate_transition(consumer_records.loads(old), consumer_metadata)
                db.execute('UPDATE jobs SET consumer_metadata=? WHERE id=?',
                           (consumer_records.dumps(consumer_metadata), identifier))
            db.execute('UPDATE jobs SET execution=? WHERE id=?', (json.dumps(snapshot, allow_nan=False), identifier))
            db.execute("UPDATE jobs SET options=? WHERE id=? AND state='running'",
                       (json.dumps(request, sort_keys=True, allow_nan=False), identifier))
            self._check_storage(db)

    def recent(self, owner):
        with self.lock:
            return {'jobs': [self._public_job(row) for row in self.db.execute(
                'SELECT * FROM jobs WHERE owner=? ORDER BY submitted DESC,rowid DESC LIMIT 50', (owner,))]}

    @staticmethod
    def _consumer_metadata(row):
        value = consumer_records.loads(row['consumer_metadata'])
        if value is None and 'consumer_request' in json.loads(row['options']):
            raise consumer_records.MetadataError('Deferred consumer metadata is missing')
        return value

    def _public_job(self, row) -> dict:
        malformed = False
        try:
            consumer = self._consumer_metadata(row) or {}
        except consumer_records.MetadataError:
            consumer, malformed = {}, True
        position = 0
        if row["state"] == "queued":
            position = self.db.execute("SELECT COUNT(*) FROM jobs WHERE state='queued' AND (submitted<? OR (submitted=? AND rowid <= (SELECT rowid FROM jobs WHERE id=?)))", (row["submitted"], row["submitted"], row["id"])).fetchone()[0]
        pending = row['state'] in {'queued', 'running', 'transfer_pending'}
        status, error = row['state'], row['error']
        available = None if pending else False
        if status == 'done':
            _, _, available = artifact_lifecycle.availability(self, row)
            if not available or row['result_expires'] is not None and row['result_expires'] <= self.clock():
                status, available = 'expired', False
                error = 'Render artifacts unavailable; recovery does not authorize another render.'
        stage = {'failed': 'failed', 'cancelled': 'cancelled', 'delivered': 'delivery'}.get(row['state'], row['stage'])
        if row['state'] == 'transfer_pending':
            stage = row['remote_phase']
        execution = json.loads(row['execution']) if row['execution'] else {}
        if stage == 'validation' and pending and 'prepare_file' in json.loads(row['options']):
            stage = 'conversion_pending'
        if stage == 'validation' and row['state'] == 'done':
            stage = 'result_ready'  # Legacy terminal row; no fabricated timing.
        condition = ('restart_required' if pending and self.process_blocked() else row['blocked_condition']) if pending else None
        diagnostic = None
        if pending and row['blocked_condition'] and row['compatibility_diagnostic'] and condition == row['blocked_condition']:
            try:
                diagnostic = sanitize(json.loads(row['compatibility_diagnostic']))
            except (ValueError, TypeError):
                diagnostic = sanitize(None)
            error = message(diagnostic)
        actions = {'restart_required':'restart_service', 'compatibility_blocked':'restore_exact_engine',
                   'execution_unknown':'inspect_execution', 'recovery_required':'recover_operation'}
        if malformed:
            condition, available = 'metadata_invalid', False
            actions['metadata_invalid'] = 'operator_inspection'
        error_category = 'metadata_invalid' if malformed else consumer.get('error_category')
        if status == 'expired':
            error_category = 'artifact_expired' if row['result_expires'] is not None and row['result_expires'] <= self.clock() else 'artifact_unavailable'
        return {**{key: consumer.get(key) for key in ('preset_decision', 'style_source', 'selection_phase', 'backend_render_started', 'selection_error', 'style_identity', 'preselection_decision')},
                'error_category': error_category,
                'error_message': consumer_errors.MESSAGES.get(error_category),
                "job_id": row["id"], "operation_id": row["id"], "status": status, "queue_position": position,
                'condition': condition, 'blocked_condition': row['blocked_condition'] if pending else None,
                'compatibility_diagnostic': diagnostic,
                'resume_phase': row['resume_phase'] if pending else None,
                'timing_integrity_conflict': bool(row['timing_conflict']),
                "stage": stage, "stage_history": json.loads(row['stage_history']),
                "result_available": available,
                "bundle_sha256": execution.get('bundle_sha256'),
                "render_spec_sha256": execution.get('request', {}).get('render_spec', {}).get('sha256'),
                "engine_build": execution.get('expected_engine', {}).get('engine_build'),
                'timings': json.loads(row['timings']),
                'child_operation_id': row['remote_id'], 'child_completed': row['remote_completed_at'] is not None,
                "session_id": row["session_id"], "submitted_by": row["submitted_by"],
                "submitted_at": row["submitted"], "started_at": row["started"], "finished_at": row["finished"],
                "cancel_requested": bool(row["cancel_requested"]), "error": error,
                "estimated_cost_usd": row["charged"] / 1_000_000,
                "expires_at": row['result_expires'],
                "next_action": actions[condition] if condition else ('recover_transfer' if status == 'transfer_pending' else ('render_status' if pending else ('render_result' if status=='done' else 'stop'))),
                "poll_after_seconds": 5 if pending else None,
                "reused": False}

    def get(self, owner: str, identifier: str) -> dict:
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise JobError("Job not found.", "not_found", 404)
            return self._public_job(row)

    def result(self, owner: str, identifier: str, *, inventory=False) -> dict:
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise JobError("Job not found.", "not_found", 404)
            try:
                self._consumer_metadata(row)
            except consumer_records.MetadataError as exc:
                raise JobError('Stored consumer metadata requires operator inspection.', 'metadata_invalid', 409) from exc
            # Persist observed loss before exposing metadata. This only reconciles
            # artifact storage; it cannot admit work or restore a tombstone.
            if row['expected_artifacts']:
                with self.transaction() as db:
                    artifact_lifecycle.reconcile_artifact_copies(self, db, row)
                artifact_lifecycle.purge(self.db, identifier)
                row = self.db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
            if not inventory and row["state"] in {"expired", "delivered", "deleted"}:
                raise JobError("Render files have expired or been deleted after delivery or a deletion request.", "expired", 410)
            if row["state"] not in {'done', 'expired', 'delivered', 'deleted'}:
                if row['state'] == 'transfer_pending':
                    raise JobError('Child execution retained; use recover-transfer for this operation', 'transfer_pending', 409)
                raise JobError("Render is not complete.", "not_ready", 409)
            if not inventory and row['result_expires'] is not None and row['result_expires'] <= self.clock():
                raise JobError("Render artifacts have expired.", "expired", 410)
            known, items, complete = artifact_lifecycle.availability(self, row)
            data = {'job_id': identifier, 'expected_artifacts': items,
                    'inventory_complete': known,
                    'artifacts': [a for a in items if a['availability'] == 'available']}
            metadata = self._public_job(row)
            data.update({key: metadata[key] for key in ('bundle_sha256', 'render_spec_sha256', 'engine_build', 'timings', 'timing_integrity_conflict', 'preset_decision', 'style_source', 'selection_phase', 'style_identity', 'preselection_decision')})
            data['bytes'] = sum(a['size'] for a in data['artifacts'])
            if not inventory and not complete:
                raise JobError('Render artifacts are unavailable; recovery never starts a replacement render.', 'artifact_unavailable', 410)
            if 'manifest' not in data:
                descriptor = next((a for a in data['artifacts'] if a['name'] == 'manifest.json'), None)
                manifest = artifact_lifecycle.valid_copy(self.db, identifier, descriptor) if descriptor else None
                data['manifest'] = json.loads(manifest.read_text()) if manifest else {}
            return data

    def delete_result(self, owner: str, identifier: str) -> dict:
        """Remove a completed result without refunding render or cost usage."""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise JobError("Job not found.", "not_found", 404)
            if row['state'] in {'queued', 'running', 'transfer_pending'}:
                raise JobError("Cancel the active render before deleting its files.", "job_in_use", 409)
            # Check both locations so a pre-privacy release's files are also
            # removed. Job IDs come from the owner-checked database record.
            states = json.loads(row['artifact_states'] or '{}')
            states = {name: 'deleted' if state == 'available' else state for name, state in states.items()}
            deleted = bool(row['result'])
            # Older results embedded full provenance in SQLite. Clear it and
            # render options; secure_delete also removes their freed cells.
            db.execute("UPDATE jobs SET result=NULL,options='{}',state='deleted',artifact_states=? WHERE id=?", (json.dumps(states), identifier))
        with self.lock:
            files, byte_count = artifact_lifecycle.delete_all_artifact_copies(self.db, identifier)
        deleted = bool(deleted or files)
        return {'job_id': identifier, 'deleted': deleted, 'files_deleted': files, 'bytes_deleted': byte_count}

    def claim(self) -> dict | None:
        if self.process_blocked():
            return None
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE (state='queued' OR (state='transfer_pending' AND recovery_ready=1)) AND blocked_condition IS NULL ORDER BY submitted,rowid LIMIT 1").fetchone()
            if not row:
                return None
            try:
                self._consumer_metadata(row)
            except consumer_records.MetadataError:
                db.execute("UPDATE jobs SET blocked_condition='recovery_required' WHERE id=?", (row['id'],))
                return None
            # A durable admission reservation already exists. Expiration or an
            # operator drain also prevents starting previously queued work.
            try:
                self._admit(db, 0, dispatch=True)
            except JobError:
                return None
            db.execute("UPDATE jobs SET state='running',started=?,recovery_ready=0 WHERE id=?", (self.clock(), row["id"]))
            from .telemetry import record
            queued = self._queue_clocks.pop(row['id'], None)
            timing = record('queue', 'controller', time.monotonic()-queued if queued is not None else 0,
                            'executed' if queued is not None else 'unavailable', row['transfer_attempt']+1)
            db.execute('UPDATE jobs SET timings=? WHERE id=?', (json.dumps((json.loads(row['timings'])+[timing])[-256:]), row['id']))
            return dict(row)

    def checkpoint(self, identifier, phase, *, dispatched=False):
        with self.transaction() as db:
            db.execute('UPDATE jobs SET resume_phase=?,dispatch_fenced=? WHERE id=?', (phase, int(dispatched), identifier))
            if phase == 'dispatch' and dispatched:
                metadata = consumer_records.loads(db.execute('SELECT consumer_metadata FROM jobs WHERE id=?', (identifier,)).fetchone()[0])
                if metadata is not None:
                    metadata['backend_render_started'] = True
                    db.execute('UPDATE jobs SET consumer_metadata=? WHERE id=?', (consumer_records.dumps(metadata), identifier))

    def block(self, identifier, condition, phase, *, diagnostic=None):
        if condition not in {'restart_required', 'compatibility_blocked', 'execution_unknown', 'recovery_required'}:
            raise ValueError('Unsupported operation condition')
        encoded = json.dumps(sanitize(diagnostic)) if diagnostic is not None else None
        with self.transaction() as db:
            db.execute("""UPDATE jobs SET blocked_condition=?,resume_phase=?,compatibility_diagnostic=?,
                state=CASE WHEN remote_completed_at IS NOT NULL OR state='transfer_pending' THEN 'transfer_pending' ELSE 'queued' END
                WHERE id=? AND state IN ('queued','running','transfer_pending')""", (condition, phase, encoded, identifier))
            # Cancellation may arrive while the backend is returning a block.
            # Settle it in this transaction; it must not need another claim.
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
            if row and row['cancel_requested'] and row['state'] in {'queued','transfer_pending'}:
                charged = row['reserved'] if row['remote_phase'] or row['dispatch_fenced'] or condition in {'execution_unknown','recovery_required'} else 0
                db.execute("UPDATE jobs SET state='cancelled',finished=?,charged=?,options='{}',error='Render cancelled by a user or operator request.' WHERE id=?", (self.clock(), charged, identifier))
                self._stage(db, identifier, 'cancelled')
                self._release_finished_inputs(db)

    def set_remote_id(self, identifier: str, remote_id: str):
        with self.transaction() as db:
            row = db.execute('SELECT remote_id FROM jobs WHERE id=?', (identifier,)).fetchone()
            if row and row['remote_id'] and row['remote_id'] != remote_id:
                raise JobError('Child operation identity is immutable', 'identity_conflict', 409)
            db.execute("UPDATE jobs SET remote_id=? WHERE id=?", (remote_id, identifier))

    def remote_checkpoint(self, identifier, phase, *, completed=False):
        with self.transaction() as db:
            db.execute('UPDATE jobs SET remote_phase=? WHERE id=?', (phase, identifier))
            if completed:
                db.execute('UPDATE jobs SET remote_completed_at=COALESCE(remote_completed_at,?) WHERE id=?', (self.clock(), identifier))

    def transfer_pending(self, identifier, phase):
        with self.transaction() as db:
            db.execute("UPDATE jobs SET state='transfer_pending',remote_phase=?,error='Child operation retained; resume observation or artifact transfer without a replacement render' WHERE id=? AND state='running'", (phase, identifier))
            if phase == 'execution_incompatible':
                db.execute("UPDATE jobs SET error='Execution engine incompatible; install the exact admitted build before recovery' WHERE id=?", (identifier,))

    def resume_transfer(self, owner, identifier):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (identifier, owner)).fetchone()
            if not row:
                raise JobError('Job not found', 'not_found', 404)
            if self.process_blocked() or row['blocked_condition']:
                raise JobError('Operation is blocked; inspect its condition', row['blocked_condition'] or 'restart_required', 409)
            if row['state'] == 'transfer_pending':
                if row['cancel_requested']:
                    raise JobError('Transfer cancelled', 'cancelled', 409)
                db.execute("UPDATE jobs SET state='queued',error=NULL,transfer_attempt=transfer_attempt+1 WHERE id=?", (identifier,))
        return self.get(owner, identifier)

    def add_timings(self, identifier, records):
        from .telemetry import sanitize
        with self.transaction() as db:
            row = db.execute('SELECT timings FROM jobs WHERE id=?', (identifier,)).fetchone()
            db.execute('UPDATE jobs SET timings=? WHERE id=?', (json.dumps(sanitize(json.loads(row['timings']) + sanitize(records))), identifier))

    def import_child_timings(self, identifier, child_id, records):
        from .telemetry import sanitize
        records = sanitize(records)
        digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self.transaction() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
            if row['remote_id'] != child_id:
                raise JobError('Child timing operation identity mismatch')
            previous = json.loads(row['child_timing_import']) if row['child_timing_import'] else None
            conflict = bool(previous and (previous['child_operation_id'] != child_id or previous['sha256'] != digest))
            if conflict:
                db.execute('UPDATE jobs SET timing_conflict=1 WHERE id=?', (identifier,))
            elif previous is None:
                marker = {'child_operation_id':child_id, 'sha256':digest,
                          'imported_at':self.clock(), 'record_count':len(records), 'records':records}
                db.execute('UPDATE jobs SET child_timing_import=?,timings=? WHERE id=?',
                           (json.dumps(marker), json.dumps(sanitize(json.loads(row['timings'])+records)), identifier))
            return bool(row['timing_conflict']) or conflict

    def cancelled(self, identifier: str) -> bool:
        with self.lock:
            row = self.db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (identifier,)).fetchone()
            return bool(row and row[0])

    def cancel(self, owner: str, identifier: str) -> dict:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                raise JobError("Job not found.", "not_found", 404)
            if row["state"] in {'queued', 'transfer_pending'}:
                db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,finished=?,options='{}',error='Render cancelled by a user or operator request.' WHERE id=?", (self.clock(), identifier))
                if row['remote_phase'] or row['dispatch_fenced'] or row['blocked_condition'] in {'execution_unknown', 'recovery_required'}:
                    db.execute('UPDATE jobs SET charged=reserved WHERE id=?', (identifier,))
                self._stage(db, identifier, 'cancelled')
            elif row["state"] == "running":
                db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (identifier,))
            self._release_finished_inputs(db)
        return self.get(owner, identifier)

    def finish(self, identifier: str, *, result: dict | None = None, error: str | None = None, seconds: float = 0, compute_started: bool = True, error_category=None):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            if not row or row["state"] != "running":
                return
            state = "cancelled" if row["cancel_requested"] else ("failed" if error else "done")
            if state == 'cancelled':
                error = 'Render cancelled by a user or operator request.'
            metadata = consumer_records.loads(row['consumer_metadata'])
            if metadata is not None and state != 'done':
                metadata['error_category'] = ('cancelled' if state == 'cancelled' else
                    metadata['error_category'] or consumer_errors.category(error_category, 'render_failed'))
                db.execute('UPDATE jobs SET consumer_metadata=? WHERE id=?', (consumer_records.dumps(metadata), identifier))
            seconds = max(0, min(float(seconds), self.policy.max_job_seconds))
            charged = min(row["reserved"], math.ceil((seconds + self.policy.startup_seconds) * self.policy.usd_per_second * 1_000_000)) if self.policy.public and compute_started else 0
            if row['remote_phase']:
                # A transfer attempt's duration is not the earlier child GPU
                # cost. Keep its committed reservation conservatively charged.
                charged = row['reserved']
            if not compute_started:
                seconds = 0
            record = {k: v for k, v in (result or {}).items() if k != 'manifest'}
            expires = (row['remote_completed_at'] or self.clock())+self.policy.ttl_seconds if state == 'done' else None
            if state == 'done':
                expected = artifact_lifecycle.inventory(record.get('artifacts', []), expires,
                                                        bool(json.loads(row['options']).get('save_blend')))
                states = {a['name']: 'available' for a in expected['artifacts']}
                db.execute('UPDATE jobs SET expected_artifacts=?,artifact_states=? WHERE id=?',
                           (json.dumps(expected), json.dumps(states), identifier))
            db.execute("UPDATE jobs SET state=?,finished=?,charged=?,gpu_seconds=?,result=?,error=?,rendered=?,result_expires=?,options='{}' WHERE id=?", (state, self.clock(), charged, seconds, json.dumps(record) if state == "done" else None, error, int(state == 'done'), expires, identifier))
            self._stage(db, identifier, 'result_ready' if state == 'done' else state)
            self._release_finished_inputs(db)

    def _release_finished_inputs(self, db):
        queued = {row['id'] for row in db.execute("SELECT id FROM jobs WHERE state='queued'")}
        self._queue_clocks = {key:value for key, value in self._queue_clocks.items() if key in queued}
        for job in db.execute("SELECT id,execution FROM jobs WHERE execution IS NOT NULL AND state NOT IN ('queued','running','transfer_pending')").fetchall():
            snapshot = json.loads(job['execution'])
            if 'bundle' in snapshot:
                snapshot.pop('bundle')
                snapshot['request'] = {'render_spec': snapshot['request'].get('render_spec', {})}
                snapshot['expected_engine'] = {'engine_build': snapshot['expected_engine']['engine_build']}
                db.execute('UPDATE jobs SET execution=? WHERE id=?', (json.dumps(snapshot), job['id']))
        transfers = self.root / 'child-results'
        if transfers.is_dir():
            for path in transfers.iterdir():
                row = db.execute('SELECT state FROM jobs WHERE id=?', (path.name,)).fetchone()
                if row is None or row['state'] not in {'queued', 'running', 'transfer_pending'}:
                    if path.is_symlink() or path.is_file():
                        path.unlink()
                    else:
                        shutil.rmtree(path)
        rows = db.execute("SELECT id FROM inputs WHERE id IN (SELECT input_id FROM jobs) AND id NOT IN (SELECT input_id FROM jobs WHERE state IN ('queued','running','transfer_pending'))").fetchall()
        for row in rows:
            self.input_path(row['id']).unlink(missing_ok=True)
            db.execute("DELETE FROM inputs WHERE id=?", (row['id'],))

    def artifact_delivered(self, owner: str, identifier: str, name: str, *, sha256=None):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if sha256 is not None and not row:
                raise JobError('Job not found.', 'not_found', 404)
            if not row or not row['expected_artifacts']:
                if sha256 is not None:
                    raise JobError('Artifact unavailable', 'artifact_unavailable', 410)
                return
            result = json.loads(row['expected_artifacts'])
            artifact = next((a for a in result['artifacts'] if a['name'] == name), None)
            if not artifact:
                if sha256 is not None:
                    raise JobError('Artifact not found', 'not_found', 404)
                return
            if sha256 is not None and artifact['sha256'] != sha256:
                raise JobError('Delivery checksum mismatch', 'checksum_mismatch', 409)
            if json.loads(row['artifact_states']).get(name) == 'delivered':
                artifact_lifecycle.delete_artifact_copies(db, identifier, name)
                return {'acknowledged': True}
            if artifact_lifecycle.effective_state(self, row, artifact) != 'available':
                if sha256 is not None:
                    raise JobError('Artifact unavailable', 'artifact_unavailable', 410)
                return
            self._stage(db, identifier, 'delivery')
            states = json.loads(row['artifact_states'])
            states[name] = 'delivered'
            db.execute('UPDATE jobs SET artifact_states=? WHERE id=?', (json.dumps(states), identifier))
            artifact_lifecycle.reconcile_artifact_copies(self, db, db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone())
        with self.lock:
            artifact_lifecycle.purge(self.db, identifier)
        return {'acknowledged': True}

    def preserve_results(self, data_root: Path) -> dict:
        """Explicit drained-controller handoff; never run from status/recovery.

        Copy unexpired outputs to a replacement data_root before shutting down
        temporary storage. The same database and original expiry remain required.
        Source files are retained until their existing deletion/expiry policy.
        """
        import tempfile
        destination = Path(data_root) / 'artifacts'
        copied = []
        with self.lock:
            if (self.db.execute("SELECT value FROM settings WHERE key='admission'").fetchone()[0] != 'draining'
                    or self.db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running','transfer_pending')").fetchone()):
                raise JobError('Drain and finish all admitted jobs before result handoff', 'jobs_active', 409)
            # Commit the managed root before copying so interrupted staging is
            # still discoverable on restart. Handoff never changes availability.
            with self.transaction() as db:
                destination = artifact_lifecycle.register_root(db, destination)
                artifact_lifecycle.sweep(self, db)
            artifact_lifecycle.purge(self.db)
            for row in self.db.execute("SELECT * FROM jobs WHERE state='done' AND result_expires>?", (self.clock(),)).fetchall():
                result = self.result(row['owner'], row['id'], inventory=True)
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                target = destination / row['id']
                if target.exists():
                    for item in result['artifacts']:
                        if item['name'] not in {'render.png', 'render.blend', 'manifest.json'}:
                            raise JobError('Unexpected artifact name')
                        path = target / item['name']
                        if target.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size != item['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                            raise JobError('Result handoff destination differs; no files overwritten')
                    copied.append(row['id'])
                    continue
                with tempfile.TemporaryDirectory(prefix='.handoff-', dir=destination) as temp:
                    staged = Path(temp) / row['id']
                    staged.mkdir(mode=0o700)
                    for item in result['artifacts']:
                        if item['name'] not in {'render.png', 'render.blend', 'manifest.json'}:
                            raise JobError('Unexpected artifact name')
                        source = artifact_lifecycle.valid_copy(self.db, row['id'], item)
                        if source is None:
                            raise JobError('Result handoff source unavailable', 'artifact_unavailable', 410)
                        data = source.read_bytes()
                        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                            raise JobError('Result handoff checksum mismatch')
                        (staged / item['name']).write_bytes(data)
                    os.replace(staged, target)
                copied.append(row['id'])
            with self.transaction() as db:
                artifact_lifecycle.sweep(self, db)
            artifact_lifecycle.purge(self.db)
        return {'job_ids': copied, 'data_root': str(Path(data_root)), 'expiry_extended': False}

    def recover_interrupted(self):
        with self.transaction() as db:
            for row in db.execute("SELECT * FROM jobs WHERE state IN ('queued','running','transfer_pending')").fetchall():
                condition = row['blocked_condition']
                child = bool(row['remote_id'] or row['remote_phase'])
                if condition in {'execution_unknown', 'recovery_required'}:
                    continue
                if row['dispatch_fenced'] and not child:
                    condition = 'execution_unknown'
                elif not row['resume_phase'] or (not row['execution'] and (child or 'prepare_file' not in json.loads(row['options']))):
                    condition = 'recovery_required'
                elif child:
                    condition = None  # Only observe the same child/lookup key.
                elif not row['execution'] and not self.input_path(row['input_id']).is_file():
                    condition = 'recovery_required'
                else:
                    from .engine import engine_identity, require_compatible, EngineMismatch
                    try:
                        if row['execution']:
                            frozen = json.loads(row['execution'])
                            require_compatible(frozen['expected_engine'], engine_identity(), frozen['request']['render_spec']['required_capabilities'])
                        condition = None
                    except (ValueError, KeyError):
                        condition = 'compatibility_blocked'
                state = 'transfer_pending' if row['remote_completed_at'] is not None else 'queued'
                # Recovery work is claimable without pretending a completed
                # child is queued for rendering; claim() handles this below.
                ready = int(child and condition is None)
                db.execute('UPDATE jobs SET state=?,blocked_condition=?,compatibility_diagnostic=?,recovery_ready=?,transfer_attempt=transfer_attempt+? WHERE id=?',
                    (state, condition, row['compatibility_diagnostic'] if condition == row['blocked_condition'] else None,
                     ready, ready, row['id']))

    def drain(self):
        with self.transaction() as db:
            db.execute("UPDATE settings SET value='draining' WHERE key='admission'")

    def resume(self):
        with self.transaction() as db:
            db.execute("UPDATE settings SET value='open' WHERE key='admission'")

    def cancel_pending(self):
        """Close queued work when operating funds no longer cover execution."""
        with self.transaction() as db:
            queued = [row['id'] for row in db.execute("SELECT id FROM jobs WHERE state='queued'")]
            db.execute("UPDATE jobs SET charged=reserved WHERE state='queued' AND (remote_phase IS NOT NULL OR dispatch_fenced=1 OR blocked_condition IN ('execution_unknown','recovery_required'))")
            db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,finished=?,options='{}',error='Service operating budget exhausted' WHERE state='queued'", (self.clock(),))
            for identifier in queued:
                self._stage(db, identifier, 'cancelled')
            self._release_finished_inputs(db)

    def cleanup(self):
        now = self.clock()
        with self.transaction() as db:
            db.execute("UPDATE jobs SET charged=reserved WHERE state='transfer_pending' AND COALESCE(remote_completed_at,submitted)+?<=?", (self.policy.ttl_seconds, now))
            db.execute("UPDATE jobs SET state='expired',options='{}',error='Child transfer expired; no replacement render authorized' WHERE state='transfer_pending' AND COALESCE(remote_completed_at,submitted)+?<=?", (self.policy.ttl_seconds, now))
            self._release_finished_inputs(db)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='oauth'").fetchone():
                db.execute("DELETE FROM oauth WHERE expires IS NOT NULL AND expires<=?", (now,))
            rows = db.execute("SELECT id FROM inputs WHERE expires<=? AND id NOT IN (SELECT input_id FROM jobs WHERE state IN ('queued','running','transfer_pending'))", (now,)).fetchall()
            for row in rows:
                self.input_path(row['id']).unlink(missing_ok=True)
                db.execute("DELETE FROM inputs WHERE id=?", (row["id"],))
            artifact_lifecycle.sweep(self, db)
        with self.lock:
            artifact_lifecycle.purge(self.db)

    def close(self):
        with self.lock:
            self.db.close()
