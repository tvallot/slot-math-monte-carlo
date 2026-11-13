"""Round-only progression and exact ordering of threshold events."""

from copy import deepcopy

import pytest

from kitchen_vs_fly.progression import ProgressionRules
from kitchen_vs_fly.validation import validate_book
from src.events.events import reveal_event
from tests.test_cascades_progression import state, controlled_draw, quiet_board


def events(state, kind):
    return [event for event in state.book.events if event["type"] == kind]


def test_initial_progress_and_no_win(state, monkeypatch):
    assert (state.progression, state.level) == (0, 1)
    def draw():
        state.board = quiet_board(state)
        state.reel_positions = [0] * 6
        reveal_event(state)
    monkeypatch.setattr(state, "draw_board", draw)
    state.run_spin(0)
    assert (state.progression, state.level) == (0, 1)
    assert len(events(state, "progressUpdate")) == 1
    assert events(state, "progressUpdate")[0]["reason"] == "roundStart"
    assert not events(state, "levelUp")
    validate_book(state.book.to_json(), state.config)


def test_accumulation_threshold_and_order(state, monkeypatch):
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    updates = events(state, "progressUpdate")
    assert [(e["previousProgress"], e["newProgress"]) for e in updates] == [(0, 0), (0, 5), (5, 10)]
    assert [(e["previousLevel"], e["newLevel"]) for e in updates] == [(1, 1), (1, 2), (2, 3)]
    assert [e["thresholdReached"] for e in events(state, "levelUp")] == [5, 10]
    assert (state.progression, state.level) == (10, 3)
    for update in updates[1:]:
        assert state.book.events[update["index"] - 1]["type"] == "tumbleBoard"
        assert state.book.events[update["index"] + 1]["type"] == "levelUp"
    validate_book(state.book.to_json(), state.config)


def test_multi_level_crossing_and_no_reactivation(state, monkeypatch):
    state.config.progression_rules = ProgressionRules(thresholds=(1, 2, 3, 4))
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    transitions = events(state, "levelUp")
    assert [e["newLevel"] for e in transitions] == [2, 3, 4, 5]
    assert [e["cascade"] for e in transitions] == [1] * 4
    assert transitions[-1]["isFinalLevel"] and transitions[-1]["stage"] == "n5_boss"
    assert not any(e["isFinalLevel"] for e in transitions[:-1])
    assert (state.progression, state.level) == (10, 5)
    assert events(state, "progressUpdate")[-1]["thresholdsReached"] == []
    assert events(state, "progressUpdate")[-1]["nextThreshold"] is None
    assert not events(state, "bossTriggered")
    validate_book(state.book.to_json(), state.config)


def test_progression_resets_between_bets(state, monkeypatch):
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    first = deepcopy(state.book.to_json())
    state.run_spin(1)
    update = events(state, "progressUpdate")[0]
    assert update["previousProgress"] == update["newProgress"] == 0
    assert update["previousLevel"] == update["newLevel"] == 1
    assert [e["newLevel"] for e in events(state, "levelUp")] == [2, 3]
    assert state.library[1] == first
    state.reset_book()
    assert (state.progression, state.level) == (0, 1)


def test_configurable_rate_and_unique_positions(state, monkeypatch):
    rules = ProgressionRules(points_per_symbol=2)
    position = {"reel": 0, "row": 0}
    assert rules.gain([{"positions": [position]}, {"positions": [position]}]) == 2
    state.config.progression_rules = rules
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    assert state.progression == 20 and state.level == 5
    validate_book(state.book.to_json(), state.config)


def test_accumulation_method_can_be_replaced(state, monkeypatch):
    class PointsPerCluster(ProgressionRules):
        def gain(self, wins):
            return len(wins)
    state.config.progression_rules = PointsPerCluster()
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    assert state.progression == 2 and state.level == 1
    validate_book(state.book.to_json(), state.config)


def test_cap_does_not_credit_symbols_that_were_not_removed(state, monkeypatch):
    state.config.wincap = 0.2
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    assert state.cascade_count == 1 and state.progression == 5
    validate_book(state.book.to_json(), state.config)


@pytest.mark.parametrize("change", ["missing_update", "duplicate_level", "bad_delta", "wrong_order"])
def test_replay_rejects_inconsistent_progression(state, monkeypatch, change):
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    book = deepcopy(state.book.to_json())
    sequence = book["events"]
    i = next(i for i, event in enumerate(sequence)
             if event["type"] == "progressUpdate" and event["reason"] == "symbolsRemoved")
    if change == "missing_update":
        sequence.pop(i)
    elif change == "duplicate_level":
        sequence.insert(i + 2, deepcopy(sequence[i + 1]))
    elif change == "bad_delta":
        sequence[i]["delta"] += 1
    else:
        sequence[i], sequence[i + 1] = sequence[i + 1], sequence[i]
    for index, event in enumerate(sequence):
        event["index"] = index
    with pytest.raises(AssertionError):
        validate_book(book, state.config)


@pytest.mark.parametrize("kwargs", [
    {"thresholds": (0, 5, 10, 15)}, {"thresholds": (5, 5, 10, 15)},
    {"thresholds": (5, 4, 10, 15)}, {"thresholds": (5, 10)},
    {"points_per_symbol": 0}, {"points_per_symbol": -1},
])
def test_invalid_development_configuration(kwargs):
    with pytest.raises(ValueError):
        ProgressionRules(**kwargs)
