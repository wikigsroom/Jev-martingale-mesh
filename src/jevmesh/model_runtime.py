"""Single-event native NanoJev inference using the fitted financial head."""
from pathlib import Path
import json
import torch
from safetensors.torch import load_file
from transformers import AutoTokenizer, Qwen3Config, Qwen3Model
from .nanojev import ChoiceHead, candidate_tokens


class EventScorer:
    def __init__(self, model_folder, head_path, device='cuda'):
        self.device = device
        folder = Path(model_folder)
        cfg = json.loads((folder / 'backbone_config/config.json').read_text(encoding='utf-8'))
        cfg['rope_theta'] = cfg.get('rope_parameters', {}).get('rope_theta', 1_000_000.)
        config = Qwen3Config(**cfg)
        config._attn_implementation = 'sdpa'
        self.backbone = Qwen3Model(config)
        weights = load_file(str(folder / 'best.safetensors'))
        self.backbone.load_state_dict({k[9:]: v for k, v in weights.items() if k.startswith('backbone.')}, assign=True)
        del weights
        dtype = torch.bfloat16 if device == 'cuda' else torch.float32
        self.backbone.to(device=device, dtype=dtype).eval()
        fitted = torch.load(head_path, map_location='cpu', weights_only=True)
        self.head = ChoiceHead().to(device).eval()
        self.head.load_state_dict(fitted['state_dict'])
        self.temperature = fitted['temperature']
        self.tokenizer = AutoTokenizer.from_pretrained(folder / 'tokenizer', local_files_only=True)

    @torch.inference_mode()
    def score(self, event):
        if list(event['candidates']) != ['up', 'range', 'down']:
            raise ValueError('Financial head requires the trained up/range/down question schema')
        paths = candidate_tokens(event, self.tokenizer)
        width = max(map(len, paths))
        if width > 1024:
            raise ValueError('Input exceeds audited input limit; no silent truncation')
        ids = torch.full((3, width), self.tokenizer.pad_token_id or self.tokenizer.eos_token_id, dtype=torch.long, device=self.device)
        lengths = torch.tensor([len(p) for p in paths], device=self.device)
        for i, p in enumerate(paths):
            ids[i, :len(p)] = torch.tensor(p, device=self.device)
        mask = torch.arange(width, device=self.device)[None, :] < lengths[:, None]
        h = self.backbone(input_ids=ids, attention_mask=mask, use_cache=False).last_hidden_state
        leaves = h[torch.arange(3, device=self.device), lengths-1].reshape(1, 3, -1).float()
        p = (self.head(leaves)/self.temperature).softmax(-1)[0].cpu().tolist()
        return dict(p_up=p[0], p_range=p[1], p_down=p[2], p_breakout=p[0]+p[2])
