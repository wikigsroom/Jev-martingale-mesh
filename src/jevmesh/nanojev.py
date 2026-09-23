"""NanoJev choice head, matching TianyuCodings/NanoJev (MIT), unified-games-v1.

Backbone embeddings are frozen. Only the decision head receives financial labels.
Labels never enter the tokenizer or the forward input.
"""
import math
import torch
from torch import nn


class ChoiceHead(nn.Module):
    def __init__(self, hidden=1024):
        super().__init__()
        self.norm = nn.LayerNorm(hidden)
        self.scalar = nn.Linear(hidden, 1)
        self.set_project = nn.Linear(hidden+1, 128)
        self.set_attention = nn.MultiheadAttention(128, 4, dropout=0., batch_first=True)
        self.set_output = nn.Linear(128, 1)

    def forward(self, leaves):
        h = self.norm(leaves)
        log_k = h.new_full((*h.shape[:2], 1), math.log(h.shape[1]))
        u = self.set_project(torch.cat([h, log_k], dim=-1))
        mixed, _ = self.set_attention(u, u, u, need_weights=False)
        return (self.scalar(h)+self.set_output(torch.tanh(u+mixed))).squeeze(-1).float()


def candidate_tokens(row, tokenizer):
    segments = [f"State:\n{row['state']}\n",
                f"Question type: choice\nQuestion:\n{row['instructions']}\n"]
    prefix = sum([tokenizer.encode(t, add_special_tokens=False) for t in segments], [])
    return [prefix+tokenizer.encode(f"Candidate:\n{k}: {v}\nDecision:", add_special_tokens=False)
            + [tokenizer.eos_token_id] for k, v in row['candidates'].items()]
