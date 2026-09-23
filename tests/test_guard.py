from jevmesh.guard import FlattenGuard


def test_cancel_then_both_close_partial_fill_retry_and_pause():
    g = FlattenGuard()
    g.trigger(1000, 60000, 'event')
    assert not g.may_open
    assert g.reconcile(.002, .003, ['pending'])[0]['action'] == 'cancel'
    actions = g.reconcile(.002, .003, [])
    assert {a['position_side'] for a in actions} == {'LONG', 'SHORT'}
    assert g.reconcile(.001, 0, []) == []  # Outstanding long is not duplicated.
    g.order_finished('LONG')
    assert g.reconcile(.001, 0, [])[0]['quantity'] == .001
    g.order_finished('LONG')
    g.reconcile(0, 0, [])
    assert g.state == 'PAUSED'
    assert not g.resume(60000, True)
    assert g.resume(61000, True)
