"""N2 "mixed Wilds" variant: optional temporary WN2T Wilds alongside the
sticky N2 Wilds, under calibration for the N2->N5 access-rate study (see
game_config.py, activate_n2_wilds in gamestate.py). Disabled by default
(`n2_temp_wild_count=0`): every test here that does not opt in confirms the
feature is a true no-op, byte-identical to the plain N2 behaviour already
covered by test_kitchen_n2_persistent_wilds.py.
"""

from copy import deepcopy

import pytest

from kitchen_vs_fly.game_events import cascade_event
from kitchen_vs_fly.validation import validate_book
from tests.test_cascades_progression import state, quiet_board, controlled_draw


@pytest.fixture
def n2_state(state):
    state.config.n2_wild_enabled = True
    return state


def n2_events(state):
    return [e for e in state.book.events if e["type"] == "wildAdded"]


def pin_sticky(state, positions):
    state.sticky_positions = set(positions)
    for c, r in positions:
        state.board[c][r] = state.create_symbol("W")


def controlled_round(state, monkeypatch):
    """Deterministic candidate picks across BOTH random.sample calls
    activate_n2_wilds now makes when n2_temp_wild_count > 0 (sticky first,
    then temporary) — a superset pool of the single-call helper used by
    test_kitchen_n2_persistent_wilds.py."""
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    pool = [(0, 1), (3, 4), (5, 5), (1, 2), (2, 5), (4, 0)]
    calls = {"i": 0}

    def choose(candidates, count):
        start = calls["i"]
        selected = pool[start:start + count]
        calls["i"] += count
        assert len(selected) == count and set(selected) <= set(candidates)
        return selected

    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample", choose)


def test_disabled_by_default_no_temp_wilds(n2_state, monkeypatch):
    assert n2_state.config.n2_temp_wild_count == 0
    n2_state.draw_board()
    selected = [(0, 1), (3, 4)]
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample", lambda candidates, k: selected)
    n2_state.level, n2_state.progression = 2, 5
    n2_state.activate_n2_wilds()
    event = n2_events(n2_state)[0]
    assert "temporaryPositions" not in event and "temporaryWildCount" not in event
    assert not any(s.name == "WN2T" for col in n2_state.board for s in col)


def test_zero_temp_count_never_calls_random_sample_twice(n2_state, monkeypatch):
    """RNG consumption must stay identical to the pre-existing single-call
    behaviour when the feature is off, even though the code path now has a
    second (skipped) sample opportunity."""
    n2_state.draw_board()
    calls = []

    def counting_sample(candidates, k):
        calls.append(k)
        return [(0, 1), (3, 4)][:k]

    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample", counting_sample)
    n2_state.config.n2_wild_count = 2
    n2_state.level, n2_state.progression = 2, 5
    n2_state.activate_n2_wilds()
    assert calls == [2]


def test_creation_places_both_sticky_and_temporary(n2_state):
    n2_state.draw_board()
    before = [[s.name for s in col] for col in n2_state.board]
    n2_state.config.n2_wild_count = 2
    n2_state.config.n2_temp_wild_count = 2
    n2_state.level, n2_state.progression = 2, 5
    pool = iter([[(0, 1), (3, 4)], [(1, 2), (2, 5)]])
    import kitchen_vs_fly.gamestate as gs
    original_sample = gs.random.sample
    calls = []

    def fake_sample(candidates, k):
        calls.append(k)
        selected = next(pool)
        assert len(selected) == k and set(selected) <= set(candidates)
        return selected

    gs.random.sample = fake_sample
    try:
        n2_state.activate_n2_wilds()
    finally:
        gs.random.sample = original_sample

    assert calls == [2, 2]
    event = n2_events(n2_state)[0]
    assert n2_state.sticky_positions == {(0, 1), (3, 4)}
    assert event["wildCount"] == 2 and event["temporaryWildCount"] == 2
    assert {(p["reel"], p["row"]) for p in event["temporaryPositions"]} == {(1, 2), (2, 5)}
    for c, r in [(0, 1), (3, 4)]:
        symbol = n2_state.board[c][r]
        assert symbol.name == "W" and symbol.check_attribute("sticky")
    for c, r in [(1, 2), (2, 5)]:
        symbol = n2_state.board[c][r]
        assert symbol.name == "WN2T" and symbol.check_attribute("wild")
        assert not symbol.check_attribute("sticky")
    assert (1, 2) not in n2_state.sticky_positions and (2, 5) not in n2_state.sticky_positions
    for c in range(6):
        for r in range(6):
            if (c, r) not in {(0, 1), (3, 4), (1, 2), (2, 5)}:
                assert n2_state.board[c][r].name == before[c][r]


def test_temporary_wild_consumed_on_win_not_sticky(n2_state):
    """A WN2T that takes part in a paid win explodes and refills like any
    ordinary symbol — unlike a sticky N2 Wild, which never does."""
    n2_state.draw_board()
    other = n2_state.config.symbols[1:]
    n2_state.board = [[n2_state.create_symbol(other[(c + r) % 5]) for r in range(6)] for c in range(6)]
    for r in range(5):
        n2_state.board[0][r] = n2_state.create_symbol("L1")
    n2_state.board[0][2] = n2_state.create_symbol("WN2T")
    n2_state.evaluate_board()
    win = next(w for w in n2_state.win_data["wins"] if w["symbol"] == "L1")
    assert win["clusterSize"] == 5
    assert {"reel": 0, "row": 2} in win["positions"]
    assert n2_state.board[0][2].explode
    n2_state.tumble_board()
    cascade_event(n2_state)
    assert n2_state.board[0][2].name != "WN2T"
    removed = n2_state.book.events[-1]["explodingSymbols"]
    assert {"reel": 0, "row": 2} in removed
    n2_state.advance_progression(n2_state.win_data["wins"])
    assert n2_state.progression == 5  # all 5 positions credited, WN2T is not excluded like a sticky Wild


def test_temporary_wild_persists_without_a_win(n2_state):
    n2_state.draw_board()
    n2_state.board[0][0] = n2_state.create_symbol("WN2T")
    background_before = n2_state.board[0][0].name
    n2_state.evaluate_board()
    assert n2_state.win_data["totalWin"] == 0
    assert n2_state.board[0][0].name == background_before == "WN2T"


def test_temporary_wild_never_becomes_sticky(n2_state):
    n2_state.draw_board()
    n2_state.config.n2_wild_count = 0
    n2_state.config.n2_temp_wild_count = 3
    n2_state.level, n2_state.progression = 2, 5
    n2_state.activate_n2_wilds()
    assert n2_state.sticky_positions == set()
    event = n2_events(n2_state)[0]
    assert event["wildCount"] == 0
    assert event["temporaryWildCount"] == 3
    temp_positions = {(p["reel"], p["row"]) for p in event["temporaryPositions"]}
    assert len(temp_positions) == 3
    for c, r in temp_positions:
        assert n2_state.board[c][r].name == "WN2T"


def test_full_round_with_mixed_wilds_replays_cleanly(n2_state, monkeypatch):
    controlled_round(n2_state, monkeypatch)
    n2_state.config.n2_wild_count = 2
    n2_state.config.n2_temp_wild_count = 2
    n2_state.run_spin(0)
    event = n2_events(n2_state)[0]
    assert event["temporaryWildCount"] == 2
    validate_book(n2_state.book.to_json(), n2_state.config)


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "wrong_source", "wrong_count"])
def test_replay_rejects_invalid_temporary_wild(n2_state, monkeypatch, corruption):
    controlled_round(n2_state, monkeypatch)
    n2_state.config.n2_wild_count = 2
    n2_state.config.n2_temp_wild_count = 2
    n2_state.run_spin(0)
    book = deepcopy(n2_state.book.to_json())
    sequence = book["events"]
    i = next(i for i, e in enumerate(sequence) if e["type"] == "wildAdded")
    if corruption == "missing":
        sequence[i]["temporaryPositions"].pop()
        sequence[i]["temporaryWildCount"] -= 1
    elif corruption == "duplicate":
        sequence[i]["temporaryPositions"].append(sequence[i]["temporaryPositions"][0])
        sequence[i]["temporaryWildCount"] += 1
    elif corruption == "wrong_source":
        sequence[i]["temporaryPositions"][0]["fromSymbol"] = "WN2T"
    else:
        sequence[i]["temporaryWildCount"] = 99
    for j, event in enumerate(sequence):
        event["index"] = j
    with pytest.raises(AssertionError):
        validate_book(book, n2_state.config)
