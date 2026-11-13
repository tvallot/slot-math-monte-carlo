"""Mechanics, controlled cascades, replay and round isolation."""

from copy import deepcopy
from collections import Counter

import pytest

from kitchen_vs_fly.game_config import GameConfig
from kitchen_vs_fly.gamestate import GameState
from kitchen_vs_fly.validation import validate_book
from src.events.events import reveal_event


@pytest.fixture
def state():
    config = GameConfig()
    config.boots_enabled = False  # Isolate the V0.2/V0.3 contracts; Boots have dedicated tests.
    config.n2_wild_enabled = False  # N2 persistent Wilds have their own dedicated tests.
    config.n3_conversion_enabled = False  # N3 premium conversion has its own dedicated tests.
    config.n4_multiplier_enabled = False  # N4 multiplier zone has its own dedicated tests.
    config.sword_enabled = False  # Sword is exercised with Boots in its focused tests.
    config.n5_boss_enabled = False  # N5 Mega Wild boss has its own dedicated tests.
    game = GameState(config)
    game.betmode = "base"
    game.criteria = "natural"
    return game


def quiet_board(state):
    return [[state.create_symbol(state.config.symbols[(c + r) % 6]) for r in range(6)] for c in range(6)]


def set_positions(state, positions, symbol="H2"):
    for c, r in positions:
        state.board[c][r] = state.create_symbol(symbol)


def test_dimensions_and_original_reels(state):
    state.draw_board()
    assert len(state.board) == 6 and all(len(col) == 6 for col in state.board)
    assert all(Counter(strip) == dict.fromkeys(state.config.symbols, 100)
               for strip in state.config.reels["DEV"])
    assert GameConfig().reels == state.config.reels


@pytest.mark.parametrize("positions,pays", [
    ([(c, 0) for c in range(5)], True),
    ([(0, r) for r in range(5)], True),
    ([(0, 0), (0, 1), (1, 1), (2, 1), (2, 2)], True),
    ([(c, c) for c in range(5)], False),
    ([(c, 0) for c in range(4)], False),
])
def test_adjacency_threshold_and_no_false_clusters(state, positions, pays):
    state.board = quiet_board(state)
    # Use L1, with a background containing only the other five symbols.
    other = state.config.symbols[1:]
    state.board = [[state.create_symbol(other[(c + r) % 5]) for r in range(6)] for c in range(6)]
    set_positions(state, positions, "L1")
    state.evaluate_board()
    assert bool(state.win_data["wins"]) == pays
    if pays:
        win = state.win_data["wins"][0]
        assert win["clusterSize"] == 5
        assert win["win"] == 0.1
    assert sum(s.explode for col in state.board for s in col) == (5 if pays else 0)


def test_full_grid_and_two_disjoint_clusters(state):
    state.board = [[state.create_symbol("H2") for _ in range(6)] for _ in range(6)]
    state.evaluate_board()
    assert state.win_data["wins"][0]["clusterSize"] == 36
    assert state.win_data["totalWin"] == 19.2
    state.reset_book()
    state.board = quiet_board(state)
    set_positions(state, [(0, r) for r in range(6)], "L1")
    set_positions(state, [(5, r) for r in range(6)], "H2")
    state.evaluate_board()
    assert len(state.win_data["wins"]) == 2
    assert state.win_data["totalWin"] == pytest.approx(sum(w["win"] for w in state.win_data["wins"]))


def controlled_draw(state):
    background = ("L3", "L4", "H1", "H2")
    state.board = [[state.create_symbol(background[(c + r) % 4]) for r in range(6)] for c in range(6)]
    state.board[0][5] = state.create_symbol("H2")
    set_positions(state, [(0, r) for r in range(5)], "L1")
    # First replacement produces a second vertical L2 cluster, then a quiet column.
    state.reelstrip = [list(background) for _ in range(6)]
    state.reelstrip[0] = ["L1", "L2", "L3", "L4", "H1", "H2"] + ["L2"] * 5
    state.reel_positions = [0] * 6
    reveal_event(state)


def test_two_cascades_gravity_totals_and_stop(state, monkeypatch):
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    book = state.book.to_json()
    assert state.cascade_count == 2
    assert state.final_win == pytest.approx(0.3)
    assert state.win_data["totalWin"] == 0
    assert not any(s.explode for col in state.board for s in col)
    cascades = [e for e in book["events"] if e["type"] == "tumbleBoard"]
    assert len(cascades[0]["explodingSymbols"]) == 5
    assert [s["name"] for s in cascades[0]["board"][0]] == ["L2"] * 5 + ["H2"]
    assert cascades[0]["board"][1:] == book["events"][0]["board"][1:]
    assert validate_book(book, state.config) == (2, False)


def test_no_win_round_and_reset(state, monkeypatch):
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    previous = deepcopy(state.book.to_json())
    def draw_quiet():
        state.board = quiet_board(state)
        state.reel_positions = [0] * 6
        reveal_event(state)
    monkeypatch.setattr(state, "draw_board", draw_quiet)
    state.run_spin(1)
    assert state.final_win == state.cascade_count == 0
    assert state.win_manager.freegame_wins == state.win_manager.basegame_wins == 0
    assert state.gametype == "basegame" and state.global_multiplier == 1
    assert not state.triggered_freegame and not state.wincap_triggered
    assert state.library[1] == previous
    assert [e["type"] for e in state.book.events] == ["reveal", "progressUpdate", "setTotalWin", "finalWin"]
    validate_book(state.book.to_json(), state.config)


def test_seed_reproducibility_and_round_isolation(state):
    state.run_spin(10, 42)
    expected = deepcopy(state.book.to_json())
    state.run_spin(20, 314)
    state.run_spin(10, 42)
    assert state.book.to_json() == expected


def test_guard_fails_without_accepting_partial_round(state, monkeypatch):
    state.config.max_cascades = 1
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    with pytest.raises(RuntimeError, match="Cascade guard"):
        state.run_spin(0)
    assert state.library == {}


def test_provisional_cap_is_explicit(state, monkeypatch):
    state.config.wincap = 0.2
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    assert state.final_win == 0.2
    assert state.wincap_triggered
    assert validate_book(state.book.to_json(), state.config) == (1, True)


def test_replay_rejects_corrupt_cascade(state, monkeypatch):
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    book = state.book.to_json()
    next(e for e in book["events"] if e["type"] == "tumbleBoard")["newSymbols"][0][0]["name"] = "H1"
    with pytest.raises(AssertionError):
        validate_book(book, state.config)
