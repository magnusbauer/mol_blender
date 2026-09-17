"""Durable artifact inventory and managed-copy lifecycle (no compute paths).

Call mutations inside the store transaction. Registered roots are operator-owned
storage, never paths taken from a render request. Inventory is written once;
only availability changes after completion.
"""
import hashlib
import json
import re
import shutil
from pathlib import Path

MEDIA = {'render.png': 'image/png', 'render.blend': 'application/octet-stream',
         'manifest.json': 'application/json', 'render.mp4': 'video/mp4',
         'render.gif': 'image/gif', 'frames.zip': 'application/zip'}
SUFFIXES = {'render.png': '.png', 'render.blend': '.blend', 'manifest.json': '.manifest.json',
            'render.mp4': '.mp4', 'render.gif': '.gif', 'frames.zip': '.frames.zip'}
FIELDS = {'render.png': 'png_base64', 'render.blend': 'blend_base64',
          'render.mp4': 'mp4_base64', 'render.gif': 'gif_base64', 'frames.zip': 'frames_base64'}

def destination(output, name):
    return Path(output) if name == 'render.png' else Path(output).with_suffix(SUFFIXES[name])



def register_root(db, path):
    # Only trusted operator configuration enters here. Freeze the target once;
    # later checks must never resolve a substituted managed root to a new target.
    path = Path(path).expanduser().resolve()
    db.execute('INSERT OR IGNORE INTO artifact_roots VALUES (?)', (str(path),))
    return path


def roots(db):
    # Refuse a root replaced by a symlink after registration as well.
    return [Path(row[0]) for row in db.execute('SELECT path FROM artifact_roots')
            if Path(row[0]).resolve() == Path(row[0])]


def directories(db, identifier):
    if not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise ValueError('Invalid stored artifact job ID')
    return [root / identifier for root in roots(db)]


def delete_artifact_copies(db, identifier, name):
    if name not in MEDIA:
        raise ValueError('Unexpected artifact name')
    for directory in directories(db, identifier):
        if directory.is_symlink():
            directory.unlink()  # Never follow a job-directory link.
        elif directory.is_dir():
            (directory / name).unlink(missing_ok=True)
            if not any(directory.iterdir()):
                directory.rmdir()


def delete_all_artifact_copies(db, identifier):
    files = size = 0
    for directory in directories(db, identifier):
        if directory.is_symlink():
            directory.unlink()
        elif directory.is_dir():
            for path in directory.iterdir():
                if path.is_file() and not path.is_symlink():
                    files += 1
                    size += path.stat().st_size
            shutil.rmtree(directory)
    return files, size


def inventory(records, expiry, scene, *, known=True, required=()):
    by_name = {a['name']: a for a in records if a.get('name') in MEDIA}
    required = set(required) | {'render.png'} | ({'render.blend'} if scene else set())
    required.update(name for name in by_name if name in ('render.mp4', 'render.gif', 'frames.zip'))
    names = set(by_name) | required
    return {'complete': known, 'artifacts': [
        {'name': name, 'sha256': by_name.get(name, {}).get('sha256'),
         'size': by_name.get(name, {}).get('size'), 'mime_type': by_name.get(name, {}).get('mime_type', MEDIA[name]),
         'required': name in required,
         'expires_at': expiry}
        for name in MEDIA if name in names]}


def valid_copy(db, identifier, item):
    for directory in directories(db, identifier):
        path = directory / item['name']
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            continue
        if path.stat().st_size == item['size'] and hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']:
            return path
    return None


def effective_state(store, row, item):
    state = json.loads(row['artifact_states'] or '{}').get(item['name'], 'lost')
    if state != 'available':
        return state
    if item['expires_at'] is None or item['expires_at'] <= store.clock():
        return 'expired'
    return 'available' if valid_copy(store.db, row['id'], item) else 'lost'


def availability(store, row):
    expected = json.loads(row['expected_artifacts'] or '{"complete": false, "artifacts": []}')
    items = [dict(item, availability=effective_state(store, row, item)) for item in expected['artifacts']]
    required = [item for item in items if item['required']]
    complete = (expected['complete'] and any(a['name'] == 'render.png' for a in required)
                and all(a['availability'] == 'available' for a in required))
    return expected['complete'], items, bool(complete)


def initialize(store, db):
    register_root(db, store.artifacts)
    register_root(db, store.root / 'artifacts')
    for row in db.execute("SELECT * FROM jobs WHERE expected_artifacts IS NULL AND (rendered=1 OR result IS NOT NULL OR state IN ('delivered','expired','deleted'))").fetchall():
        result = json.loads(row['result'] or '{}')
        records = result.get('artifacts', [])
        manifest = result.get('manifest', {})
        descriptor = next((a for a in records if a.get('name') == 'manifest.json'), None)
        if not manifest and descriptor:
            path = valid_copy(db, row['id'], descriptor)
            if path:
                try:
                    manifest = json.loads(path.read_text())
                except (ValueError, OSError):
                    pass
        original = manifest.get('artifacts', [])
        receipts = list(db.execute('SELECT name,sha256 FROM delivery_receipts WHERE job_id=? AND owner=?', (row['id'], row['owner'])))
        all_records = [dict(a) for a in receipts] + records + original
        scene = any(a.get('name') == 'render.blend' for a in all_records)
        expected = inventory(all_records, row['result_expires'], scene, known=bool(original))
        delivered = {a['name'] for a in receipts}
        current = {a['name'] for a in records}
        # Before C2, delivery removed descriptors from result.artifacts. The
        # original manifest supplies expectations, not permission to restore.
        if row['state'] == 'done' and isinstance(result.get('artifacts'), list):
            delivered.update(a['name'] for a in original if a['name'] not in current)
        states = {a['name']: ('delivered' if a['name'] in delivered else
                  row['state'] if row['state'] in {'delivered', 'deleted', 'expired'} else
                  'available' if a['name'] in current else 'lost')
                  for a in expected['artifacts']}
        db.execute('UPDATE jobs SET expected_artifacts=?,artifact_states=? WHERE id=?',
                   (json.dumps(expected), json.dumps(states), row['id']))


def reconcile_artifact_copies(store, db, row):
    expected = json.loads(row['expected_artifacts'] or '{"artifacts": []}')
    states = json.loads(row['artifact_states'] or '{}')
    for item in expected['artifacts']:
        states[item['name']] = effective_state(store, row, item)
    if states != json.loads(row['artifact_states'] or '{}'):
        db.execute('UPDATE jobs SET artifact_states=? WHERE id=?', (json.dumps(states), row['id']))
    if not any(s == 'available' for s in states.values()):
        if row['state'] == 'done':
            state = 'delivered' if states and all(s == 'delivered' for s in states.values()) else 'expired'
            db.execute('UPDATE jobs SET state=?,result=NULL WHERE id=?', (state, row['id']))


def sweep(store, db):
    for row in db.execute('SELECT * FROM jobs WHERE expected_artifacts IS NOT NULL').fetchall():
        reconcile_artifact_copies(store, db, row)


def purge(db, identifier=None):
    """Delete only after tombstones commit; interrupted deletion is retryable."""
    query = 'SELECT * FROM jobs WHERE expected_artifacts IS NOT NULL'
    for row in db.execute(query + (' AND id=?' if identifier else ''), (identifier,) if identifier else ()).fetchall():
        states = json.loads(row['artifact_states'] or '{}')
        for name, state in states.items():
            if state != 'available':
                delete_artifact_copies(db, row['id'], name)
        if not any(s == 'available' for s in states.values()):
            delete_all_artifact_copies(db, row['id'])
    if identifier:
        return
    # The entire registered artifact root is managed storage. Unknown job
    # directories and abandoned atomic handoff staging are never publishable.
    for root in roots(db):
        if not root.is_dir():
            continue
        for path in root.iterdir():
            orphan = re.fullmatch(r'[0-9a-f]{32}', path.name) and not db.execute('SELECT 1 FROM jobs WHERE id=?', (path.name,)).fetchone()
            if orphan or path.name.startswith('.handoff-'):
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
