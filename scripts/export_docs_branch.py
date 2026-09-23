"""Export committed research documentation into a separate local docs repository."""
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'https://github.com/wikigsroom/Jev-martingale-mesh'


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args]).decode('utf-8').strip()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def export(output):
    target = output.resolve()
    if target == ROOT or target.is_relative_to(ROOT) or ROOT.is_relative_to(target):
        raise ValueError('Use a separate directory outside the source checkout')
    revision = git('rev-parse', 'HEAD')
    if git('status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Commit tracked source changes before exporting documentation')
    tracked = {p for p in subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z']).decode('utf-8').split('\0') if p}
    selected = set()
    for name in tracked:
        p = Path(name)
        if name.startswith('docs/') and p.suffix == '.md':
            selected.add(name)
        elif name.startswith('reports/fmz_v2/') and p.suffix in ['.md', '.html', '.json', '.csv', '.png', '.svg']:
            selected.add(name)
        elif name.startswith('records/fmz-v2/') and p.suffix == '.json':
            selected.add(name)
        elif name.startswith('THIRD_PARTY_NOTICES/'):
            selected.add(name)
        elif name in ['data/audit/single_event_runtime.json', 'data/audit/model_training.json',
                       'data/audit/event_labels.json', 'data/audit/final_data_audit.json', '.gitattributes']:
            selected.add(name)

    def link(source, value):
        enclosed = value.startswith('<') and value.endswith('>')
        raw = value[1:-1] if enclosed else value
        parsed = urlsplit(raw)
        if parsed.scheme or raw.startswith(('#', '//')) or not parsed.path:
            return value
        destination = (source.parent / unquote(parsed.path)).resolve()
        if not destination.is_relative_to(ROOT):
            raise ValueError(f'Link points outside the source tree: {source}: {value}')
        relative = destination.relative_to(ROOT).as_posix()
        if relative in selected:
            return value
        if relative not in tracked:
            raise ValueError(f'Link has no committed destination: {source}: {value}')
        url = f'{REPOSITORY}/blob/{revision}/{quote(relative, safe="/")}'
        return url+('#'+parsed.fragment if parsed.fragment else '')

    payloads = {}
    origin_hashes = {}
    for name in sorted(selected):
        source = ROOT / name
        data = source.read_bytes()
        origin_hashes[name] = sha(data)
        if source.suffix in ['.md', '.html']:
            text = data.decode('utf-8')
            if source.suffix == '.md':
                text = re.sub(r'(\]\()(<[^>]+>|[^)\n]+)(\))',
                    lambda m: m[1]+link(source, m[2])+m[3], text)
            else:
                text = re.sub(r'(\b(?:href|src)=[\"\x27])([^\"\x27]+)([\"\x27])',
                    lambda m: m[1]+link(source, m[2])+m[3], text)
            data = text.encode('utf-8')
        payloads[name] = data
    payloads['README.md'] = f'''# Jev Martingale Mesh · 研究文档

这是独立的`docs`分支。程序与测试见[main分支]({REPOSITORY}/tree/main)，本次文档对应程序提交[`{revision[:12]}`]({REPOSITORY}/commit/{revision})。

| 入口 | 内容 |
| --- | --- |
| [方案总览](docs/fmz-v2/README.md) | FMZ-JEV-20260923-R1版本与证据入口 |
| [方案及决策记录](docs/fmz-v2/strategy_record.md) | 参数、策略流程、选参依据和失败情景 |
| [JEV的作用](docs/fmz-v2/jev_role.md) | 事件过滤、趋势分工与模型增量证据 |
| [完整报告](reports/fmz_v2/final_report_zh.md) | 秒级回测、对照、分月结果及压力测试 |
| [最终参数](reports/fmz_v2/final_parameters.json) | 候选1623的完整机器可读配置 |
| [原策略审查](reports/fmz_v2/source_review/review_zh.md) | FMZ源码问题与BTC适配边界 |
| [复现和归档](docs/fmz-v2/reproduction.md) | 本地命令、数据要求及冻结记录 |
| [Git分支说明](docs/repository_layout.md) | 程序和文档两个本地仓库的维护方式 |

本版在2026-03-01至09-21的基础成本模拟中，100U变为172.61U，最大回撤17.09%，模拟爆仓0次。费用加倍且滑点10bp时为−21.53%，新闻延迟60秒时为−22.06%；每条新闻固定暂停的规则对照收益高于JEV方案。这里保留全部这些结论，历史收益不代表未来盈利。

本分支提供阅读副本与参数证据，不包含程序运行代码、行情或模型权重。未复制的源码和ZIP归档链接指向上述固定程序提交。Markdown/HTML中的链接可能已调整；原始冻结哈希对应`main`中的原始文件，导出前后哈希记录在[documentation_manifest.json](documentation_manifest.json)。

文档由程序仓库的`scripts/export_docs_branch.py`导出。请在程序仓库更新后重新导出；脚本会保护文档仓库中的手工修改。
'''.encode('utf-8')
    payloads['.gitignore'] = b'.DS_Store\nThumbs.db\n.env\n.env.*\n.ssh/\n*.pem\n*.key\n'
    manifest_path = target / 'documentation_manifest.json'
    old = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'files': []}
    previous = {r['path']: r['export_sha256'] for r in old['files']}
    removed = set(previous)-set(payloads)
    if removed:
        raise ValueError(f'Review obsolete exported files before updating: {sorted(removed)}')
    # Validate every overwrite before writing any output.
    for name, data in payloads.items():
        destination = target / name
        if destination.exists() and destination.read_bytes() != data:
            if name not in previous or sha(destination.read_bytes()) != previous[name]:
                raise ValueError(f'Preserving a manual edit in the documentation checkout: {name}')
    for name, data in payloads.items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    manifest = {'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'source_repository': REPOSITORY, 'source_revision': revision,
        'source_branch': git('branch', '--show-current'), 'destination_branch': 'docs',
        'files': [{'path': name, 'source_sha256': origin_hashes.get(name), 'export_sha256': sha(data)}
                  for name, data in sorted(payloads.items())]}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(target), 'files': len(payloads)+1,
                      'source_revision': revision}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    export(parser.parse_args().output)
