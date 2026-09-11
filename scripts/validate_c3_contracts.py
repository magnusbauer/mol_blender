"""Offline application contracts and an outside-checkout installed-wheel check.

Run with the prepared test interpreter. Build/installation are explicit runner
activities; every pytest process loads its audit guard before application imports.
No dependency fetching, render, SSH, scheduler or provider action is authorized.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ['test_execution_envelope.py', 'test_gateway_recovery.py', 'test_hpc_negotiation.py',
             'test_hpc_lifecycle.py', 'test_runtime_contracts.py', 'test_worker_http.py',
             'test_operation_lifecycle.py', 'test_result_recovery.py', 'test_jobs_api.py',
             'test_cli_operation_routing.py', 'test_gateway_isolation.py', 'test_c3_inprocess.py',
             'test_engine_package.py', 'test_c3_integrity.py', 'test_c3_operation_blocks.py',
             'test_c3_connections.py', 'test_c3_recovery_integration.py', 'test_c3_policy.py', 'test_c3_migrations.py',
             'test_c3_review_regressions.py', 'test_c3_final_boundaries.py',
             'test_c3_blocked_clients.py', 'test_c3_stored_retrieval.py', 'test_c3_ccd_capture.py',
             'test_developer_ccd_entry.py', 'test_worker_ipc.py', 'test_hpc_outer_ipc.py', 'test_acceptance_ipc.py', 'test_acceptance_diagnostics.py', 'test_compatibility_diagnostics.py',
             'test_selection.py', 'test_spec.py', 'test_reference_presets.py', 'test_style_schema.py',
             'test_developer.py', 'test_developer_policy.py']


def run(command, cwd, env, log):
    with log.open('w') as stream:
        subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if out.is_relative_to(ROOT):
        raise ValueError('Use temporary storage outside the checkout')
    out.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'BLENDER_RENDER_HOME':str(out/'home'),
           'PYMOL_BRIDGE_SESSION_ROOT':str(out/'sessions'), 'PYMOL_BRIDGE_TMP_ROOT':str(out/'runs'),
           'PYTHONPATH':str(ROOT/'tests')}
    pytest = [sys.executable, '-m', 'pytest', '-q', '-rs', '-p', 'no:cacheprovider', '-p', 'c3_boundary_guard']
    run(pytest, ROOT, env, out/'repository.log')
    staging = out/'source'
    staging.mkdir()
    import runpy
    inventory_api = runpy.run_path(str(ROOT/'blender_render/engine.py'))
    generate = inventory_api['generate_manifest']
    for name in inventory_api['BUILD_INPUTS']:
        (staging/name).parent.mkdir(parents=True, exist_ok=True)
        if (ROOT/name).is_dir():
            shutil.copytree(ROOT/name, staging/name)
        else:
            shutil.copy2(ROOT/name, staging/name)
    # Stage only package source/resources, never ignored render caches or SIFs.
    inventory_api['stage_package'](ROOT/'blender_render', staging/'blender_render')
    run([sys.executable, '-m', 'build', '--wheel', '--no-isolation', '--outdir', str(out/'wheels')],
        staging, env, out/'build.log')
    wheel = next((out/'wheels').glob('*.whl'))
    site = out/'site'
    run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps', '--target', str(site), str(wheel)],
        out, env, out/'install.log')
    shutil.copytree(ROOT/'tests', out/'tests', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (out/'scripts').mkdir()
    shutil.copy2(ROOT/'scripts/validate_c3_acceptance.py', out/'scripts/validate_c3_acceptance.py')
    env['PYTHONPATH'] = os.pathsep.join([str(site), str(out/'tests')])
    env['C3_INSTALLED_SITE'] = str(site)
    run([*pytest, *(str(out/'tests'/name) for name in CONTRACTS)], out, env, out/'installed.log')
    with zipfile.ZipFile(wheel) as archive:
        manifest = json.loads(archive.read('blender_render/engine-manifest.json'))
        extracted = out/'wheel-inventory'
        for member in archive.infolist():
            if not member.filename.startswith('blender_render/') or member.is_dir():
                continue
            name = Path(member.filename)
            if '..' in name.parts or '\\' in member.filename:
                raise ValueError('Invalid package path in built wheel')
            target = extracted/name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError('Duplicate package path in built wheel')
            target.write_bytes(archive.read(member))
        inventory = inventory_api['package_inventory']
        assert inventory(ROOT/'blender_render') == inventory(extracted/'blender_render') == inventory(site/'blender_render') == manifest['files']
        for name, digest in manifest['files'].items():
            data = archive.read('blender_render/'+name)
            assert hashlib.sha256(data).hexdigest() == digest
            assert data == (ROOT/'blender_render'/name).read_bytes() == (site/'blender_render'/name).read_bytes()
    # Separate interpreter activities belong to this runner, never to the
    # guarded application. Every probe loads c3_boundary_guard before imports.
    probes = []
    for label, package_parent, cwd, reverse, seed in (
            ('source', ROOT, ROOT, False, '1'),
            ('source-reordered', ROOT, out, True, '91'),
            ('staged-reordered', staging, out, True, '17'),
            ('installed', site, out, False, '103')):
        probe_env = {**env, 'PYTHONPATH':os.pathsep.join([str(package_parent), str(out/'tests')]),
                     'PYTHONHASHSEED':seed, 'D1_EXPECT_PACKAGE':str(package_parent/'blender_render')}
        command = [sys.executable, str(out/'tests/d1_determinism_probe.py')]
        if reverse:
            command.append('--reverse')
        log = out/(label+'-determinism.json')
        run(command, cwd, probe_env, log)
        probes.append(json.loads(log.read_text()))
    assert all(probe == probes[0] for probe in probes), 'D1 catalog/decision determinism failed'
    evidence = {'catalog_digest':probes[0]['catalog']['digest'],
                'determinism_interpreters':len(probes), 'determinism_boundary_attempts':[],
                'wheel_sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
                'engine_build':manifest['engine_build'], 'verified_package_files':len(manifest['files']),
                'repository_log':'repository.log', 'installed_log':'installed.log',
                'application_boundaries':'guarded; no real compute',
                'installed_dependencies':'prepared runner dependencies; not a clean dependency install'}
    (out/'evidence.json').write_text(json.dumps(evidence, indent=2)+'\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
