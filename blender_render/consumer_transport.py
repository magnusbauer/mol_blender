"""Version 1 D2 descriptors, deliberately narrower than the D1 library API."""
import json
import math
import re

MAX_REQUEST_BYTES = 64 * 1024
MAX_OVERRIDE_BYTES = 32 * 1024
MAX_DEPTH = 12
MAX_ITEMS = 32
MAX_DESCRIPTORS = 64
MAX_TOKEN = 64
VOCABULARIES = {
    'material_family': ('clay', 'frosted', 'milky', 'dried_paint', 'directional_ink'),
    'background': ('solid', 'gradient', 'transparent'),
    'lighting': ('area', 'sun', 'point', 'spot'),
    'edges': ('smooth', 'mixed', 'rounded'),
    'composition': ('structure', 'focus_style', 'depth_of_field'),
    'projection': ('ORTHO', 'PERSP'),
    'ligand_treatment': ('sticks',),
}


def bounded_json(value, *, limit=MAX_REQUEST_BYTES):
    count = 0
    def walk(item, depth):
        nonlocal count
        count += 1
        if depth > MAX_DEPTH or count > 4096:
            raise ValueError('Request structure exceeds its bounds')
        if isinstance(item, dict):
            for key, child in item.items():
                if type(key) is not str or len(key) > 128:
                    raise ValueError('Invalid object key')
                walk(child, depth + 1)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ValueError('Invalid JSON value')
    walk(value, 0)
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode()) > limit:
        raise ValueError('Request exceeds its encoded size limit')
    return encoded


def color(value):
    if isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        return value.upper()
    if isinstance(value, str) and len(value) <= 128 and value.startswith('linear_rgb:'):
        rgb = json.loads(value[len('linear_rgb:'):])
        if isinstance(rgb, list) and len(rgb) == 3 and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 16 for v in rgb):
            return 'linear_rgb:' + json.dumps([float(v) for v in rgb], separators=(',', ':'))
    raise ValueError('Invalid canonical color')


def descriptors(value):
    from .selection import DESCRIPTOR_ALIASES
    total = 0
    for section in ('hard_requirements', 'visual_brief'):
        data = value.get(section, {})
        if not isinstance(data, dict):
            raise ValueError('Expected structured descriptors')
        for field, items in data.items():
            if field in {'content', 'representations', 'required_capabilities', 'palette'}:
                if not isinstance(items, list) or len(items) > MAX_ITEMS:
                    raise ValueError('Descriptor collection exceeds its bound')
                total += len(items)
                normalized = []
                for item in items:
                    if field == 'palette':
                        normalized.append(color(item))
                    elif (not isinstance(item, str) or len(item) > MAX_TOKEN
                          or not re.fullmatch(r'[a-z][a-z0-9_.-]*' if field == 'required_capabilities' else r'[a-z][a-z0-9_]*', item)):
                        raise ValueError('Invalid descriptor identifier')
                    else:
                        normalized.append(item)
                data[field] = sorted(set(normalized))
            elif section == 'visual_brief' and field in VOCABULARIES and items is not None:
                total += 1
                if not isinstance(items, str) or len(items) > 128:
                    raise ValueError('Invalid visual descriptor')
                normalized = DESCRIPTOR_ALIASES.get(field, {}).get(items, items)
                if field == 'background' and normalized.startswith('#'):
                    normalized = color(normalized)
                elif normalized not in VOCABULARIES[field]:
                    raise ValueError('Unknown visual descriptor')
                data[field] = normalized
    if total > MAX_DESCRIPTORS:
        raise ValueError('Too many descriptors')
    bounded_json(value.get('overrides', {}), limit=MAX_OVERRIDE_BYTES)
    return value
