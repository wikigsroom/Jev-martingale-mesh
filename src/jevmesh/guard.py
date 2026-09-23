"""Transport-independent paper execution guard. It never sends exchange orders.

Cancellation acknowledgements are not proof of flat inventory. The caller must
reconcile positions and open orders before this guard declares PAUSED.
"""
from dataclasses import dataclass, field


@dataclass
class FlattenGuard:
    state: str = 'RUNNING'
    resume_after_ms: int = 0
    reason: str = ''
    generation: int = 0
    outstanding: set = field(default_factory=set)

    @property
    def may_open(self):
        return self.state == 'RUNNING'

    def trigger(self, now_ms, hold_ms, reason):
        self.resume_after_ms = max(self.resume_after_ms, now_ms+hold_ms)
        self.reason = reason
        if self.state in ('RUNNING', 'PAUSED'):
            self.generation += 1
            self.state = 'CANCELLING'
        return {'action': 'cancel_all_opening_orders', 'generation': self.generation}

    def reconcile(self, long_qty, short_qty, open_order_ids):
        if self.state == 'RUNNING':
            return []
        if open_order_ids:
            self.state = 'CANCELLING'
            return [{'action': 'cancel', 'order_id': oid} for oid in open_order_ids]
        actions = []
        for side, qty in [('LONG', long_qty), ('SHORT', short_qty)]:
            if qty > 0 and side not in self.outstanding:
                # Intent only. A venue adapter must map hedge-side closing semantics.
                actions.append({'action': 'close_only', 'position_side': side, 'quantity': qty,
                                'client_id': f'flatten-{self.generation}-{side}'})
                self.outstanding.add(side)
        if long_qty == 0 and short_qty == 0:
            self.state = 'PAUSED'
            self.outstanding.clear()
        else:
            self.state = 'FLATTENING'
        return actions

    def order_finished(self, side):
        # A partial fill or rejection allows a retry after a fresh reconciliation.
        self.outstanding.discard(side)

    def resume(self, now_ms, market_healthy):
        if self.state == 'PAUSED' and now_ms >= self.resume_after_ms and market_healthy:
            self.state = 'RUNNING'
        return self.may_open
