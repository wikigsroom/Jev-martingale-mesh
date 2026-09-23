import numpy as np
from jevmesh import dataset


def test_event_never_arrives_before_delay_and_old_events_are_not_replayed(monkeypatch):
    monkeypatch.setattr(dataset, 'news_scores', lambda kind: ([0, 1000, 5001], [.9, .8, .7]))
    times = np.arange(5000, 13000, 1000)
    signals = dataset.add_signals(times, 'nanojev', 1)
    assert signals[0] == 0  # Past events don't get incorrectly squeezed into start.
    assert signals[1] == 0  # 5001 + 1000 is after 6000.
    assert signals[2] == .7
