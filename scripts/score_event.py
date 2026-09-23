from pathlib import Path
import argparse
import json
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jevmesh.model_runtime import EventScorer

parser = argparse.ArgumentParser()
parser.add_argument('--input', required=True, help='One question JSON with state/instructions/up-range-down candidates; no future inputs')
parser.add_argument('--device', default='cuda', choices=['cuda', 'cpu'])
args = parser.parse_args()
scorer = EventScorer(ROOT / 'models/nanojev-unified', ROOT / 'models/event_head.pt', args.device)
event = json.loads(Path(args.input).read_text(encoding='utf-8'))
started = time.perf_counter()
result = scorer.score(event)
result['elapsed_ms_including_tokenization'] = (time.perf_counter()-started)*1000
print(json.dumps(result, indent=2))
