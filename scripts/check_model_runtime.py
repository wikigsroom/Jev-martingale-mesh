"""Check independent single-event execution against the batch inference artifact."""
from pathlib import Path
import json
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jevmesh.model_runtime import EventScorer
row = json.loads((ROOT / 'examples/event_question.json').read_text(encoding='utf-8'))
expected = json.loads((ROOT / 'data/processed/event_predictions.jsonl').read_text(encoding='utf-8').splitlines()[0])
scorer = EventScorer(ROOT / 'models/nanojev-unified', ROOT / 'models/event_head.pt')
scorer.score(row)  # Warm-up is separate from the measured second call.
start = time.perf_counter()
actual = scorer.score(row)
latency = (time.perf_counter()-start)*1000
error = max(abs(actual[k]-expected[k]) for k in actual)
assert error < .005, (actual, expected)
result = {'single_event_warm_latency_ms': latency, 'max_probability_difference_to_batch': error,
          'predictions': actual, 'input_scope': 'One event, three candidates; includes tokenizer but excludes source/network/order transport'}
(ROOT / 'data/audit/single_event_runtime.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result), flush=True)
