"""Archive an existing FMZ research result; never rerun or silently replace it."""
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import importlib.metadata
import json
import platform
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'reports/fmz_v2'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def inventory(paths):
    return [{'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size,
             'sha256': digest(p)} for p in sorted(set(paths))]


def verify(folder):
    folder = Path(folder).resolve()
    manifest = read(folder / 'manifest.json')
    archive = folder / manifest['archive']['file']
    if digest(archive) != manifest['archive']['sha256']:
        raise ValueError('Archive hash differs from recorded version')
    checked = 0
    with zipfile.ZipFile(archive) as zf:
        expected = {item['path'] for item in manifest['archived_files']}
        if set(zf.namelist()) != expected:
            raise ValueError('Archive member list differs from recorded version')
        for item in manifest['archived_files']:
            with zf.open(item['path']) as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != item['sha256'] or zf.getinfo(item['path']).file_size != item['bytes']:
                raise ValueError(f"Archived file differs: {item['path']}")
            checked += 1
    for item in manifest['external_dependencies']:
        path = ROOT / item['path']
        if not path.is_file() or digest(path) != item['sha256']:
            raise ValueError(f"External dependency missing or changed: {item['path']}")
    receipt = {'passed': True, 'verified_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'record_id': manifest['record_id'], 'archive_sha256': manifest['archive']['sha256'],
        'manifest_sha256': digest(folder / 'manifest.json'), 'archived_files_verified': checked,
        'external_dependencies_verified': len(manifest['external_dependencies'])}
    write(folder / 'verification.json', receipt)
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


def create(record_id):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}-r\d+', record_id):
        raise ValueError('Use a record ID such as 2026-09-23-r1')
    target = ROOT / 'records/fmz-v2' / record_id
    if target.exists():
        raise FileExistsError(f'Refusing to replace an existing record: {target}')
    freeze = read(RESULTS / 'selection_freeze.json')
    result = read(RESULTS / 'evaluation.json')
    for path, expected in freeze['sources'].items():
        if digest(ROOT / path) != expected:
            raise ValueError(f'Frozen source or input changed: {path}')
    for item in freeze['artifacts'].values():
        if digest(RESULTS / item['file']) != item['parameter_sha256']:
            raise ValueError('Parameters changed after selection')
    if digest(RESULTS / 'selection_freeze.json') != result['selection_freeze_sha256']:
        raise ValueError('Evaluation belongs to a different freeze')
    audit = read(RESULTS / 'delivery_audit.json')
    if not audit['passed'] or audit['evaluation_sha256'] != digest(RESULTS / 'evaluation.json'):
        raise ValueError('Evaluation has no matching successful delivery audit')
    if audit['report_sha256'] != digest(RESULTS / 'final_report_zh.md'):
        raise ValueError('Report changed after delivery audit')
    included = [ROOT / name for name in ['README.md', 'pyproject.toml', '.gitignore', 'reports/evaluation.json']]
    for directory in ['src', 'scripts', 'tests']:
        included.extend(p for p in (ROOT / directory).rglob('*') if p.is_file() and p.suffix in ['.py', '.cjs'])
    for directory in ['docs/fmz-v2', 'THIRD_PARTY_NOTICES', '参考内容/FMZ.COM', 'data/audit',
                      'reports/fmz_v2/source_review', 'reports/fmz_v2/search_margin_verified']:
        included.extend(p for p in (ROOT / directory).rglob('*') if p.is_file())
    included.extend(p for p in RESULTS.iterdir() if p.is_file())
    dependencies = []
    for directory in ['data/raw', 'data/processed', 'models']:
        dependencies.extend(p for p in (ROOT / directory).rglob('*') if p.is_file()
                            and not any(part in ['.cache', '__pycache__', '.git'] for part in p.parts))
    print(json.dumps({'stage': 'hashing', 'archived_files': len(set(included)),
                      'external_dependencies': len(set(dependencies))}), flush=True)
    archived_inventory = inventory(included)
    external_inventory = inventory(dependencies)
    target.mkdir(parents=True, exist_ok=False)
    record_date, revision = record_id.rsplit('-', 1)
    version = f"{record_date.replace('-', '')}-{revision}"
    archive_name = f"fmz-jev-{version}.zip"
    archive = target / archive_name
    print(json.dumps({'stage': 'writing_archive', 'path': str(archive)}), flush=True)
    with zipfile.ZipFile(archive, mode='x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in archived_inventory:
            zf.write(ROOT / item['path'], arcname=item['path'])
    libraries = {}
    for package in ['numpy', 'pandas', 'numba', 'scipy', 'requests', 'matplotlib', 'markdown', 'pytest']:
        try:
            libraries[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            libraries[package] = 'not installed in this interpreter'
    manifest = {'record_id': f"FMZ-JEV-{version.upper()}",
        'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'workspace_at_creation': str(ROOT), 'status': 'Research backtest; profitable under base assumptions, cost and latency stresses failed',
        'selected_trial': freeze['artifacts']['selected']['trial'],
        'scope': 'Code, documentation and existing research evidence. Large data/model dependencies are hashed references, not bundled.',
        'base_result': result['full_1s']['selected'],
        'adverse_result': result['stress_full_1s']['combined_double_fees_10bps'],
        'parameter_sha256': freeze['artifacts']['selected']['parameter_sha256'],
        'event_ablation_sha256': digest(RESULTS / 'event_ablation.json'),
        'event_ablation_provenance': 'Existing frozen-parameter diagnostic from the preceding research; archived now without rerunning or modifying it.',
        'runtime': {'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(), 'libraries': libraries},
        'archive': {'file': archive_name, 'bytes': archive.stat().st_size, 'sha256': digest(archive)},
        'archived_files': archived_inventory, 'external_dependencies': external_inventory}
    write(target / 'manifest.json', manifest)
    verify(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--record-id')
    group.add_argument('--verify', type=Path)
    args = parser.parse_args()
    if args.verify:
        verify(args.verify)
    else:
        create(args.record_id)
