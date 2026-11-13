"""N3 (active fly hunting): premium symbol in-place conversion, edge cases, replay.

Updated design (see GAME_DESIGN.md, 2026-09-17): N3 no longer removes symbols,
applies gravity or refills the grid. It inspects the premium types (H1-H4)
currently on the board, picks one already-present type as the target, and
converts every other premium type into it in place; low symbols and N2
persistent Wilds are never touched. `activate_n3_conversion` computes the
full set of positions to convert from the board *before* mutating anything
(a single comprehension over the untouched board), so the conversion is
provably simultaneous rather than sequential — no cell's post-conversion
name can influence whether another cell gets converted.
"""

from copy import deepcopy

import pytest

from kitchen_vs_fly.progression import ProgressionRules
from kitchen_vs_fly.validation import validate_book
from tests.test_cascades_progression import state, quiet_board
from src.events.events import reveal_event


@pytest.fixture
def n3_state(state):
    state.config.n3_conversion_enabled = True
    return state


@pytest.fixture
def n2_n3_state(state):
    state.config.n2_wild_enabled = True
    state.config.n3_conversion_enabled = True
    return state


def n3_events(state):
    return [e for e in state.book.events if e["type"] == "gridTransform"]


def pin(state, positions):
    state.sticky_positions = set(positions)
    for c, r in positions:
        state.board[c][r] = state.create_symbol("W")


def scattered_board(state, premium_cells=None):
    """A 6x6 board of a safe diagonal low-symbol background (no two orthogonal
    neighbours ever share a symbol, since (c+r) changes by 1 between them),
    with explicit cells overridden by `premium_cells` ({(reel, row): name})."""
    background = ("L3", "L4", "L1", "L2")
    state.board = [[state.create_symbol(background[(c + r) % 4]) for r in range(6)] for c in range(6)]
    for (c, r), name in (premium_cells or {}).items():
        state.board[c][r] = state.create_symbol(name)


def controlled_draw_ten_cluster(state):
    """A single 2x5 connected L1 cluster (10 distinct positions) so that, under
    the default DEV thresholds (5/10/15/20), progression jumps straight from 0
    to 10 in one cascade — crossing the N2 (5) and N3 (10) thresholds at once."""
    background = ("L3", "L4", "H1", "H2")
    state.board = [[state.create_symbol(background[(c + r) % 4]) for r in range(6)] for c in range(6)]
    for c in (0, 1):
        for r in range(5):
            state.board[c][r] = state.create_symbol("L1")
    state.reelstrip = [list(background) for _ in range(6)]
    state.reel_positions = [0] * 6
    reveal_event(state)


def test_no_trigger_below_level_three(n3_state, monkeypatch):
    n3_state.draw_board()
    n3_state.level, n3_state.progression = 2, 9
    def unexpected(*args, **kwargs):
        raise AssertionError("N3 must not consume RNG below level 3")
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", unexpected)
    n3_state.activate_n3_conversion()
    assert not n3_events(n3_state) and not n3_state.n3_activated


def test_activates_once_and_second_call_is_noop(n3_state, monkeypatch):
    n3_state.draw_board()
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    n3_state.level, n3_state.progression = 3, 10
    n3_state.activate_n3_conversion()
    assert len(n3_events(n3_state)) == 1
    n3_state.activate_n3_conversion()
    assert len(n3_events(n3_state)) == 1


def test_target_selected_only_among_premium_types_present(n3_state, monkeypatch):
    scattered_board(n3_state, {(0, 0): "H1", (2, 0): "H2", (4, 0): "H4", (5, 5): "H1"})
    seen = {}
    def choose(seq):
        seen["present"] = list(seq)
        return "H2"
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", choose)
    n3_state.level, n3_state.progression = 3, 10
    n3_state.activate_n3_conversion()
    assert seen["present"] == ["H1", "H2", "H4"], "H3 is absent and must never be offered as a candidate"
    event = n3_events(n3_state)[0]
    assert event["premiumTypesPresent"] == ["H1", "H2", "H4"]
    assert event["targetPremium"] == "H2"


def test_all_other_premiums_become_target_low_symbols_untouched(n3_state, monkeypatch):
    premium_cells = {(0, 0): "H1", (2, 0): "H2", (4, 0): "H4", (5, 5): "H1"}
    scattered_board(n3_state, premium_cells)
    before_low = {(c, r): n3_state.board[c][r].name for c in range(6) for r in range(6)
                  if (c, r) not in premium_cells}
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: "H2")
    n3_state.level, n3_state.progression = 3, 10
    n3_state.activate_n3_conversion()
    event = n3_events(n3_state)[0]
    expected_touched = {(0, 0), (4, 0), (5, 5)}  # every H1/H4 cell; the existing H2 stays as-is
    assert {(p["reel"], p["row"]) for p in event["positions"]} == expected_touched
    assert event["convertedCount"] == 3
    for (c, r), name in before_low.items():
        assert n3_state.board[c][r].name == name, "low symbols must never be touched by N3"
    for c, r in expected_touched:
        assert n3_state.board[c][r].name == "H2"
    assert n3_state.board[2][0].name == "H2", "the already-target cell must remain unchanged"


def test_n2_wilds_excluded_from_premium_detection_and_conversion(n3_state, monkeypatch):
    scattered_board(n3_state, {(0, 0): "H1", (2, 3): "H2"})
    pin(n3_state, [(1, 0), (3, 3)])
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    n3_state.level, n3_state.progression = 3, 10
    n3_state.activate_n3_conversion()
    event = n3_events(n3_state)[0]
    assert "W" not in event["premiumTypesPresent"]
    touched = {(p["reel"], p["row"]) for p in event["positions"]}
    assert (1, 0) not in touched and (3, 3) not in touched
    assert n3_state.board[1][0].name == "W" and n3_state.board[3][3].name == "W"
    assert n3_state.sticky_positions == {(1, 0), (3, 3)}


def test_no_premium_symbols_present_is_a_deterministic_no_op(n3_state, monkeypatch):
    scattered_board(n3_state)
    def unexpected(seq):
        raise AssertionError("No premium present: target selection must not run")
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", unexpected)
    n3_state.level, n3_state.progression = 3, 10
    before = [[s.name for s in col] for col in n3_state.board]
    n3_state.activate_n3_conversion()
    event = n3_events(n3_state)[0]
    assert event["premiumTypesPresent"] == []
    assert event["targetPremium"] is None
    assert event["positions"] == [] and event["convertedCount"] == 0
    assert n3_state.n3_activated
    assert [[s.name for s in col] for col in n3_state.board] == before


def test_single_premium_type_present_is_a_deterministic_no_op(n3_state, monkeypatch):
    scattered_board(n3_state, {(0, 0): "H3", (2, 4): "H3", (5, 1): "H3"})
    seen = {}
    def choose(seq):
        seen["present"] = list(seq)
        return seq[0]
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", choose)
    n3_state.level, n3_state.progression = 3, 10
    n3_state.activate_n3_conversion()
    assert seen["present"] == ["H3"]
    event = n3_events(n3_state)[0]
    assert event["targetPremium"] == "H3"
    assert event["positions"] == [] and event["convertedCount"] == 0


@pytest.mark.parametrize("present_types", [("H1", "H2"), ("H1", "H2", "H4"), ("H1", "H2", "H3", "H4")])
def test_multiple_premium_types_all_converted_to_target(n3_state, monkeypatch, present_types):
    cycle = list(present_types) + ["L1", "L2", "L3", "L4"]
    grid = [[cycle[(r * 6 + c) % len(cycle)] for c in range(6)] for r in range(6)]
    n3_state.board = [[n3_state.create_symbol(grid[r][c]) for r in range(6)] for c in range(6)]
    target = present_types[0]
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: target)
    n3_state.level, n3_state.progression = 3, 10
    n3_state.activate_n3_conversion()
    event = n3_events(n3_state)[0]
    assert event["premiumTypesPresent"] == sorted(present_types)
    assert event["targetPremium"] == target
    for c in range(6):
        for r in range(6):
            name = n3_state.board[c][r].name
            if grid[r][c] in present_types:
                assert name == target
            else:
                assert name == grid[r][c]


def test_conversion_creates_a_new_winning_cluster(n3_state, monkeypatch):
    premium_cells = {(0, 0): "H1", (1, 0): "H1", (0, 1): "H2", (1, 1): "H1", (0, 2): "H2"}
    scattered_board(n3_state, premium_cells)
    n3_state.evaluate_board()
    assert n3_state.win_data["totalWin"] == 0, "H1 (3) and H2 (2) are not payable clusters yet"
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: "H1")
    n3_state.level, n3_state.progression = 3, 10
    n3_state.activate_n3_conversion()
    n3_state.evaluate_board()
    win = next(w for w in n3_state.win_data["wins"] if w["symbol"] == "H1")
    assert win["clusterSize"] == 5
    assert n3_state.win_data["totalWin"] > 0


def test_conversion_enlarges_an_existing_cluster(n3_state, monkeypatch):
    premium_cells = {(0, 0): "H2", (1, 0): "H2", (0, 1): "H2", (1, 1): "H2", (0, 2): "H2", (1, 2): "H4"}
    scattered_board(n3_state, premium_cells)
    n3_state.evaluate_board()
    before_win = next(w for w in n3_state.win_data["wins"] if w["symbol"] == "H2")
    assert before_win["clusterSize"] == 5
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: "H2")
    n3_state.level, n3_state.progression = 3, 10
    n3_state.activate_n3_conversion()
    n3_state.evaluate_board()
    after_win = next(w for w in n3_state.win_data["wins"] if w["symbol"] == "H2")
    assert after_win["clusterSize"] == 6, "the H4 cell adjacent to the cluster joins it once converted"


def test_conversion_can_produce_no_winning_cluster(n3_state, monkeypatch):
    scattered_board(n3_state, {(0, 0): "H1", (2, 0): "H2", (4, 0): "H4", (0, 2): "H3"})
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: "H1")
    n3_state.level, n3_state.progression = 3, 10
    n3_state.activate_n3_conversion()
    n3_state.evaluate_board()
    assert n3_state.win_data["totalWin"] == 0, "the four converted cells remain mutually non-adjacent"


def test_n2_wild_helps_complete_cluster_created_by_n3(n2_n3_state, monkeypatch):
    premium_cells = {(0, 0): "H1", (1, 0): "H2", (2, 0): "H2", (0, 1): "H2"}
    scattered_board(n2_n3_state, premium_cells)
    pin(n2_n3_state, [(1, 1)])  # N2 persistent Wild already present, adjacent to the future cluster
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: "H2")
    n2_n3_state.level, n2_n3_state.progression = 3, 10
    n2_n3_state.activate_n3_conversion()
    assert n2_n3_state.board[1][1].name == "W", "N2 Wilds must survive N3 untouched"
    n2_n3_state.evaluate_board()
    win = next(w for w in n2_n3_state.win_data["wins"] if w["symbol"] == "H2")
    assert {"reel": 1, "row": 1} in win["positions"]
    assert win["clusterSize"] == 5
    assert not n2_n3_state.board[1][1].explode, "the Wild contributes but is never removed"


def test_trigger_once_and_full_replay_order(n3_state, monkeypatch):
    monkeypatch.setattr(n3_state, "draw_board", lambda: controlled_draw_ten_cluster(n3_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    n3_state.run_spin(0)
    assert len(n3_events(n3_state)) == 1
    event = n3_events(n3_state)[0]
    assert event["progression"] >= 10
    assert n3_state.book.events[event["index"] - 1]["type"] == "levelUp"
    validate_book(n3_state.book.to_json(), n3_state.config)


def test_simultaneous_n2_and_n3_threshold_crossing(n2_n3_state, monkeypatch):
    monkeypatch.setattr(n2_n3_state, "draw_board", lambda: controlled_draw_ten_cluster(n2_n3_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    n2_n3_state.run_spin(0)
    events = n2_n3_state.book.to_json()["events"]
    level_ups = [e for e in events if e["type"] == "levelUp"]
    assert [e["newLevel"] for e in level_ups[:2]] == [2, 3], "one 10-point cascade crosses both thresholds"
    i_wild = next(e["index"] for e in events if e["type"] == "wildAdded")
    i_grid = next(e["index"] for e in events if e["type"] == "gridTransform")
    assert i_grid == i_wild + 1, "N3 runs immediately after N2 within the same activation sequence"
    assert events[i_wild - 1]["type"] == "levelUp" and events[i_wild - 1]["newLevel"] == 3
    validate_book(n2_n3_state.book.to_json(), n2_n3_state.config)


def test_n4_n5_gameplay_effects_remain_disabled(n2_n3_state, monkeypatch):
    """N2 and N3 are the only active Kitchen level features in this milestone:
    crossing later thresholds must still only emit progression events, never a
    grid-changing effect (old Sword/Boss stay off by default, and no N4/N5
    Kitchen feature exists yet)."""
    monkeypatch.setattr(n2_n3_state, "draw_board", lambda: controlled_draw_ten_cluster(n2_n3_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    n2_n3_state.config.progression_rules = ProgressionRules(thresholds=(1, 2, 3, 4))
    n2_n3_state.run_spin(0)
    event_types = {e["type"] for e in n2_n3_state.book.events}
    assert event_types & {"wildAdded", "gridTransform"}
    assert not event_types & {"swordActivated", "bossTriggered"}
    assert n2_n3_state.level == 5
    validate_book(n2_n3_state.book.to_json(), n2_n3_state.config)


def test_reset_between_bets_reactivates_n3_independently(n3_state, monkeypatch):
    monkeypatch.setattr(n3_state, "draw_board", lambda: controlled_draw_ten_cluster(n3_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    n3_state.run_spin(0, 42)
    assert len(n3_events(n3_state)) == 1
    n3_state.run_spin(1, 987)
    assert len(n3_events(n3_state)) == 1, "a fresh round must not skip N3 as 'already activated'"
    def draw_quiet():
        n3_state.board = quiet_board(n3_state)
        n3_state.reel_positions = [0] * 6
        reveal_event(n3_state)
    monkeypatch.setattr(n3_state, "draw_board", draw_quiet)
    n3_state.run_spin(2)
    assert not n3_state.n3_activated and not n3_events(n3_state)
    assert n3_state.progression == 0 and n3_state.level == 1
    validate_book(n3_state.book.to_json(), n3_state.config)


def test_deterministic_target_selection_for_fixed_seed(n3_state, monkeypatch):
    monkeypatch.setattr(n3_state, "draw_board", lambda: controlled_draw_ten_cluster(n3_state))
    n3_state.run_spin(0, 42)
    first = deepcopy(n3_state.book.to_json())
    n3_state.run_spin(0, 42)
    assert n3_state.book.to_json() == first
    validate_book(first, n3_state.config)


def test_grid_transform_is_the_public_n3_event(n3_state, monkeypatch):
    monkeypatch.setattr(n3_state, "draw_board", lambda: controlled_draw_ten_cluster(n3_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    n3_state.run_spin(0)
    event_types = {e["type"] for e in n3_state.book.events}
    assert "gridTransform" in event_types
    assert not event_types & {"swordActivated", "bossTriggered", "bootsActivated"}


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "wrong_target", "wrong_source", "wrong_board"])
def test_replay_rejects_invalid_n3(n3_state, monkeypatch, corruption):
    monkeypatch.setattr(n3_state, "draw_board", lambda: controlled_draw_ten_cluster(n3_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    n3_state.run_spin(0)
    book = deepcopy(n3_state.book.to_json())
    sequence = book["events"]
    i = next(i for i, e in enumerate(sequence) if e["type"] == "gridTransform")
    if corruption == "missing":
        sequence.pop(i)
    elif corruption == "duplicate":
        sequence.insert(i, deepcopy(sequence[i]))
    elif corruption == "wrong_target":
        present = sequence[i]["premiumTypesPresent"]
        if len(present) >= 4:
            pytest.skip("all four premium types present; cannot craft an absent target")
        sequence[i]["targetPremium"] = next(h for h in ("H1", "H2", "H3", "H4") if h not in present)
    elif corruption == "wrong_source":
        if not sequence[i]["positions"]:
            pytest.skip("no-op activation has no positions to corrupt")
        sequence[i]["positions"][0]["fromSymbol"] = "L1"
    else:
        sequence[i]["board"][0][0] = {**sequence[i]["board"][0][0], "corrupted": True}
    for j, event in enumerate(sequence):
        event["index"] = j
    with pytest.raises(AssertionError):
        validate_book(book, n3_state.config)


def test_n3_n4_n5_gameplay_effects_remain_disabled_n3_only(n3_state, monkeypatch):
    """Same guarantee as above but with N3 alone (no N2), confirming N3 does
    not accidentally depend on N2 being active."""
    monkeypatch.setattr(n3_state, "draw_board", lambda: controlled_draw_ten_cluster(n3_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    n3_state.config.progression_rules = ProgressionRules(thresholds=(1, 2, 3, 4))
    n3_state.run_spin(0)
    event_types = {e["type"] for e in n3_state.book.events}
    assert event_types & {"gridTransform"}
    assert not event_types & {"swordActivated", "bossTriggered", "wildAdded"}
    assert n3_state.level == 5
    validate_book(n3_state.book.to_json(), n3_state.config)
