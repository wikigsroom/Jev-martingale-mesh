"""Read the supplied FMZ multi-tag export without executing its trading code."""
from pathlib import Path
import re
import json
import hashlib
ROOT = Path(__file__).resolve().parents[1]
source = next((ROOT / '参考内容/FMZ.COM').glob('*.xml'))
raw = source.read_text(encoding='utf-8')
code = re.search(r'<CODE>\s*([\s\S]*?)\s*</CODE>', raw).group(1)
params = json.loads(re.search(r'<PARAM>\s*([\s\S]*?)\s*</PARAM>', raw).group(1))
backtest = json.loads(re.search(r'<BACKTEST>\s*([\s\S]*?)\s*</BACKTEST>', raw).group(1))
out = ROOT / 'reports/fmz_v2/source_review'
out.mkdir(parents=True, exist_ok=True)
(out / 'original.js').write_text(code+'\n', encoding='utf-8')
(out / 'export_parameters.json').write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding='utf-8')
(out / 'export_backtest.json').write_text(json.dumps(backtest, ensure_ascii=False, indent=2), encoding='utf-8')
facts = {'source': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
         'source_lines': len(raw.splitlines()), 'code_lines': len(code.splitlines()),
         'export_symbol': backtest['symbol'], 'export_capital': backtest['accountBalance'],
         'backtest_overrides': dict(json.loads(backtest['args'])),
         'defaults': {p['name']: p['default'] for p in params['args']['parameters']}}
(out / 'source_manifest.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(facts, ensure_ascii=False, indent=2))
