"""Run actual NanoJev on a native CUDA Python, then fit/calibrate its event head."""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import sys
import time
import numpy as np
import torch
from safetensors.torch import load_file
from transformers import AutoTokenizer, Qwen3Config, Qwen3Model

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jevmesh.nanojev import ChoiceHead, candidate_tokens


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8*1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def extract(rows, batch):
    folder = ROOT / "models/nanojev-unified"
    cache = ROOT / "data/processed/nanojev_features.npz"
    source_hash = digest(ROOT / "data/processed/event_questions.json")
    if cache.exists():
        d = np.load(cache)
        if str(d['source_hash']) == source_hash:
            return d['features'], d['original_logits'], d['latency_ms']
    expected = "f68c47d66998231b86b7e91b4ed5e82ae23acf104c8b7cd6d165c3ac7b7ffe1b"
    if digest(folder / "best.safetensors") != expected:
        raise ValueError("NanoJev weight SHA256 differs from verified release")
    if not torch.cuda.is_available():
        raise RuntimeError("Use a native CUDA Python runtime; no virtualization is needed")
    tokenizer = AutoTokenizer.from_pretrained(folder / "tokenizer", local_files_only=True)
    cfg = json.loads((folder / "backbone_config/config.json").read_text(encoding="utf-8"))
    cfg['rope_theta'] = cfg.get('rope_parameters', {}).get('rope_theta', 1_000_000.)
    config = Qwen3Config(**cfg)
    config._attn_implementation = "sdpa"
    weights = load_file(str(folder / "best.safetensors"))
    backbone = Qwen3Model(config)
    backbone.load_state_dict({k[len('backbone.'):]: v for k, v in weights.items() if k.startswith('backbone.')}, assign=True, strict=True)
    head = ChoiceHead(config.hidden_size)
    head.load_state_dict({k: v for k, v in weights.items() if not k.startswith('backbone.')}, strict=True)
    torch.save(head.state_dict(), ROOT / "models/original_choice_head.pt")
    del weights
    backbone = backbone.to(device='cuda', dtype=torch.bfloat16).eval()
    head = head.to('cuda').eval()
    paths = [candidate_tokens(r, tokenizer) for r in rows]
    max_tokens = max(len(p) for group in paths for p in group)
    if max_tokens > 1024:
        raise ValueError(f"No silent token truncation; maximum {max_tokens}")
    embeddings, logits, latencies = [], [], []
    start = time.perf_counter()
    with torch.inference_mode():
        for first in range(0, len(rows), batch):
            group = paths[first:first+batch]
            flat = [p for g in group for p in g]
            lengths = torch.tensor([len(p) for p in flat], device='cuda')
            width = int(lengths.max())
            tokens = torch.full((len(flat), width), tokenizer.pad_token_id or tokenizer.eos_token_id, dtype=torch.long, device='cuda')
            for j, p in enumerate(flat):
                tokens[j, :len(p)] = torch.tensor(p, device='cuda')
            mask = torch.arange(width, device='cuda')[None, :] < lengths[:, None]
            torch.cuda.synchronize()
            t = time.perf_counter()
            h = backbone(input_ids=tokens, attention_mask=mask, use_cache=False).last_hidden_state
            leaves = h[torch.arange(len(flat), device='cuda'), lengths-1].reshape(len(group), 3, -1).float()
            z = head(leaves)
            torch.cuda.synchronize()
            latencies.append((time.perf_counter()-t)*1000)
            embeddings.append(leaves.cpu().numpy().astype('float16'))
            logits.append(z.cpu().numpy())
            if first % (batch*20) == 0:
                print(json.dumps({'questions_done': first+len(group), 'total': len(rows), 'elapsed_s': round(time.perf_counter()-start, 1)}), flush=True)
    features, original = np.concatenate(embeddings), np.concatenate(logits)
    np.savez_compressed(cache, features=features, original_logits=original,
                        source_hash=source_hash, latency_ms=np.array(latencies))
    dump(ROOT / "data/audit/nanojev_inference.json", {
        'release': 'C-Tianyu/NanoJev unified-games-v1', 'weight_sha256': expected,
        'device': torch.cuda.get_device_name(), 'torch': torch.__version__,
        'precision': 'BF16 backbone, float32 choice head', 'questions': len(rows),
        'max_tokens_per_candidate': max_tokens, 'batch_questions': batch,
        'batch_latency_p50_ms': float(np.median(latencies[1:])),
        'batch_latency_p95_ms': float(np.percentile(latencies[1:], 95)),
        'scope': 'Tokenized batch GPU inference only; excludes publication/transport/tokenization/order latency',
        'feature_input_sha256': source_hash})
    del backbone, head
    torch.cuda.empty_cache()
    return features, original, np.array(latencies)


def metrics(probs, labels):
    if len(labels) == 0:
        return {}
    target = np.eye(3)[labels]
    return {'n': len(labels), 'accuracy': float((probs.argmax(1) == labels).mean()),
            'log_loss': float(-np.log(probs[np.arange(len(labels)), labels].clip(1e-12)).mean()),
            'multiclass_brier': float(((probs-target)**2).sum(1).mean()),
            'label_counts': np.bincount(labels, minlength=3).tolist()}


def fit(rows, features, original):
    torch.set_num_threads(2)
    torch.manual_seed(20260923)
    x = torch.tensor(features.astype('float32'))
    y = torch.tensor([r['label'] for r in rows], dtype=torch.long)
    dates = np.array([r['decision_ms'] for r in rows])
    def ms(d):
        return int(np.datetime64(d, 'ms').astype('int64'))
    fit_mask = dates < ms('2026-06-01')
    select_mask = (dates >= ms('2026-06-01')) & (dates < ms('2026-06-16'))
    cal_mask = (dates >= ms('2026-06-16')) & (dates < ms('2026-07-01'))
    assert fit_mask.any() and select_mask.any() and cal_mask.any()
    original_state = torch.load(ROOT / "models/original_choice_head.pt", weights_only=True)
    best_loss, best, trials, chosen = float('inf'), None, [], None
    for lr, wd in [(1e-4, .1), (3e-4, .1), (1e-4, 1.), (3e-4, 1.)]:
        torch.manual_seed(20260923)
        head = ChoiceHead().train()
        head.load_state_dict(original_state)
        opt = torch.optim.AdamW(head.parameters(), lr=lr, weight_decay=wd)
        tx, ty = x[fit_mask], y[fit_mask]
        local_loss, stale = float('inf'), 0
        for epoch in range(160):
            head.train()
            for idx in torch.randperm(len(tx)).split(32):
                opt.zero_grad(set_to_none=True)
                loss = torch.nn.functional.cross_entropy(head(tx[idx]), ty[idx])
                loss.backward()
                torch.nn.utils.clip_grad_norm_(head.parameters(), 1.)
                opt.step()
            head.eval()
            with torch.no_grad():
                val = float(torch.nn.functional.cross_entropy(head(x[select_mask]), y[select_mask]))
            if val < local_loss-1e-5:
                local_loss, stale = val, 0
            else:
                stale += 1
            if val < best_loss:
                best_loss, best = val, copy.deepcopy(head.state_dict())
                chosen = {'learning_rate': lr, 'weight_decay': wd, 'epoch': epoch+1}
            if stale >= 18:
                break
        trials.append({'learning_rate': lr, 'weight_decay': wd, 'epochs_run': epoch+1, 'best_selection_log_loss': local_loss})
        print(json.dumps(trials[-1]), flush=True)
    head.load_state_dict(best)
    head.eval()
    with torch.no_grad():
        z = head(x)
    temperatures = np.geomspace(.25, 4., 81)
    cal_losses = [float(torch.nn.functional.cross_entropy(z[cal_mask]/float(t), y[cal_mask])) for t in temperatures]
    temperature = float(temperatures[int(np.argmin(cal_losses))])
    probs = (z/temperature).softmax(-1).numpy()
    original_probs = torch.tensor(original).softmax(-1).numpy()
    prior = np.bincount(y[fit_mask].numpy(), minlength=3)/int(fit_mask.sum())
    masks = {'fit_Mar_May': fit_mask, 'head_selection_Jun_1_15': select_mask,
             'calibration_Jun_16_30': cal_mask,
             'strategy_validation_Jul_Aug': (dates >= ms('2026-07-01')) & (dates < ms('2026-09-01'))}
    # September diagnostic evaluation is deliberately deferred until parameters freeze.
    stats = {name: {'adapted': metrics(probs[mask], y.numpy()[mask]),
                    'original': metrics(original_probs[mask], y.numpy()[mask]),
                    'class_prior': metrics(np.tile(prior, (int(mask.sum()), 1)), y.numpy()[mask])}
             for name, mask in masks.items()}
    torch.save({'state_dict': best, 'temperature': temperature, 'class_order': ['up', 'range', 'down']}, ROOT / 'models/event_head.pt')
    records = []
    for row, p, op in zip(rows, probs, original_probs):
        records.append({'event_id': row['event_id'], 'available_ms': row['available_ms'],
                        'decision_ms': row['decision_ms'], 'input_last_market_ms': row['input_last_market_ms'],
                        'p_up': float(p[0]), 'p_range': float(p[1]), 'p_down': float(p[2]),
                        'p_breakout': float(p[0]+p[2]), 'original_p_range': float(op[1]),
                        'label': row['label']})
    (ROOT / 'data/processed/event_predictions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records), encoding='utf-8')
    dump(ROOT / 'data/audit/model_training.json', {
        'method': 'Frozen NanoJev backbone plus supervised retraining of its original choice head',
        'seed': 20260923, 'chosen': chosen, 'temperature': temperature, 'trials': trials,
        'evaluation': stats, 'head_sha256': digest(ROOT / 'models/event_head.pt'),
        'unseen_strategy_holdout': '2026-09-01 to last complete market day',
        'warning': 'Small single-publisher sample, retrospective timestamps, and possible backbone pretraining contamination. No causal-news or profitability claim.'})
    print(json.dumps({'selected': chosen, 'temperature': temperature, 'validation': stats['strategy_validation_Jul_Aug']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=int, default=3)
    args = parser.parse_args()
    rows = json.loads((ROOT / 'data/processed/event_questions.json').read_text(encoding='utf-8'))
    features, original, _ = extract(rows, args.batch)
    fit(rows, features, original)
