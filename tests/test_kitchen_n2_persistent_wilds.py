"""N2 (roast chicken protection): persistent Wilds, sticky gravity, Wild wins, replay.

Generalizes the old Armor sticky-Wild mechanism (see
the development notes) at its correct Kitchen vs Fly
level: N2 fires at level 2 (the first dev threshold), not the old level 3.
The underlying sticky-position gravity/cluster machinery in `gamestate.py`
is generic (not feature-specific) and is exercised directly here via the
`pin()` helper, independent of which feature created the Wilds.
"""

from copy import deepcopy

import pytest

from kitchen_vs_fly.game_events import cascade_event
from kitchen_vs_fly.progression import ProgressionRules
from kitchen_vs_fly.validation import validate_book, winning_clusters
from tests.test_cascades_progression import state, quiet_board, controlled_draw
from src.events.events import reveal_event


@pytest.fixture
def n2_state(state):
    state.config.n2_wild_enabled = True
    return state


def pin(state, positions):
    state.sticky_positions = set(positions)
    for c, r in positions:
        state.board[c][r] = state.create_symbol("W")


def n2_events(state):
    return [e for e in state.book.events if e["type"] == "wildAdded"]


def controlled_round(state, monkeypatch):
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    def choose(candidates, count):
        selected = [(0, 1), (3, 4), (5, 5)][:count]
        assert len(selected) == count and set(selected) <= set(candidates)
        return selected
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample", choose)


def test_no_trigger_below_level_two(n2_state, monkeypatch):
    assert not n2_state.n2_wilds_activated and not n2_state.sticky_positions
    n2_state.level, n2_state.progression = 1, 4
    def unexpected(*args):
        raise AssertionError("N2 must not consume RNG below level 2")
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample", unexpected)
    n2_state.activate_n2_wilds()
    assert not n2_events(n2_state)


def test_creation_only_changes_selected_normal_positions(n2_state, monkeypatch):
    n2_state.draw_board()
    before = [[s.name for s in col] for col in n2_state.board]
    selected = [(0, 1), (3, 4), (5, 5)]
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample", lambda candidates, k: selected)
    n2_state.level, n2_state.progression = 2, 5
    n2_state.activate_n2_wilds()
    event = n2_events(n2_state)[0]
    assert event["wildCount"] == 3
    assert n2_state.sticky_positions == set(selected)
    for c in range(6):
        for r in range(6):
            symbol = n2_state.board[c][r]
            assert symbol.name == ("W" if (c, r) in selected else before[c][r])
            if (c, r) in selected:
                assert symbol.check_attribute("wild") and symbol.check_attribute("sticky")
    assert event["positions"] == [
        {"reel": c, "row": r, "fromSymbol": before[c][r], "toSymbol": "W"} for c, r in selected]
    assert n2_state.progression == 5 and n2_state.win_manager.running_bet_win == 0
    n2_state.activate_n2_wilds()
    assert len(n2_events(n2_state)) == 1


def test_two_wild_variant_is_configurable(n2_state, monkeypatch):
    n2_state.draw_board()
    n2_state.config.n2_wild_count = 2
    selected = [(0, 1), (3, 4)]
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample", lambda candidates, k: selected)
    n2_state.level, n2_state.progression = 2, 5
    n2_state.activate_n2_wilds()
    event = n2_events(n2_state)[0]
    assert event["wildCount"] == 2
    assert n2_state.sticky_positions == set(selected)


def test_gravity_skips_two_fixed_slots_and_refills_in_reel_order(n2_state):
    n2_state.draw_board()
    n2_state.board = quiet_board(n2_state)
    n2_state.reel_positions = [0] * 6
    n2_state.reelstrip = [list(n2_state.config.symbols) for _ in range(6)]
    pin(n2_state, [(0, 1), (0, 4)])
    anchors = [n2_state.board[0][1], n2_state.board[0][4]]
    other_columns = [col[:] for col in n2_state.board[1:]]
    for r in (0, 3, 5):
        n2_state.board[0][r].explode = True
    n2_state.tumble_board()
    # Reel cursor starts at 0 and decrements before each read (wrapping over
    # all 8 symbols): three refills give H2, H3, H4 in that fall order.
    assert [s.name for s in n2_state.board[0]] == ["H2", "W", "H3", "H4", "W", "L3"]
    assert [s.name for s in n2_state.new_symbols_from_tumble[0]] == ["H2", "H3", "H4"]
    assert n2_state.board[1:] == other_columns
    assert n2_state.reel_positions == [5, 0, 0, 0, 0, 0]
    for _ in range(3):
        n2_state.board[0][5].explode = True
        n2_state.tumble_board()
        assert n2_state.board[0][1] is anchors[0] and n2_state.board[0][4] is anchors[1]
        assert not any(s.explode for s in n2_state.board[0])


def test_wild_completes_cluster_but_is_not_removed_or_credited(n2_state):
    n2_state.draw_board()
    other = n2_state.config.symbols[1:]
    n2_state.board = [[n2_state.create_symbol(other[(c + r) % 5]) for r in range(6)] for c in range(6)]
    for r in range(5):
        n2_state.board[0][r] = n2_state.create_symbol("L1")
    pin(n2_state, [(0, 2)])
    n2_state.evaluate_board()
    win = next(w for w in n2_state.win_data["wins"] if w["symbol"] == "L1")
    assert win["clusterSize"] == 5 and win["win"] == 0.1
    assert {"reel": 0, "row": 2} in win["positions"]
    assert not n2_state.board[0][2].explode
    assert sum(s.explode for col in n2_state.board for s in col) == 4
    n2_state.tumble_board()
    cascade_event(n2_state)
    assert n2_state.board[0][2].name == "W"
    removed = n2_state.book.events[-1]["explodingSymbols"]
    assert len(removed) == 4 and {"reel": 0, "row": 2} not in removed
    n2_state.advance_progression(n2_state.win_data["wins"])
    assert n2_state.progression == 4


def test_shared_wild_counts_in_two_clusters_only_normals_explode(n2_state):
    background = ("L3", "L4", "H1", "H2")
    n2_state.board = [[n2_state.create_symbol(background[(c + r) % 4]) for r in range(6)] for c in range(6)]
    for c in (0, 1, 3, 4):
        n2_state.board[c][2] = n2_state.create_symbol("L1")
    for r in (0, 1, 3, 4):
        n2_state.board[2][r] = n2_state.create_symbol("L2")
    pin(n2_state, [(2, 2)])
    n2_state.evaluate_board()
    assert {w["symbol"] for w in n2_state.win_data["wins"]} == {"L1", "L2"}
    assert all(w["clusterSize"] == 5 for w in n2_state.win_data["wins"])
    assert sum(s.explode for col in n2_state.board for s in col) == 8
    n2_state.advance_progression(n2_state.win_data["wins"])
    assert n2_state.progression == 8
    snapshot = [[{"name": s.name} for s in col] for col in n2_state.board]
    assert sum(w[2] for w in winning_clusters(snapshot, n2_state.config)) == 30


def test_wild_only_board_does_not_pay(n2_state):
    """A grid covered entirely by persistent Wilds must never pay on its own:
    with no regular symbol anywhere, no cluster can be seeded (see also the
    `count=40` case of test_configurable_count_and_all_wild_board_has_no_own_pay,
    which reaches the same all-Wild state through activate_n2_wilds itself)."""
    n2_state.draw_board()
    pin(n2_state, [(c, r) for c in range(6) for r in range(6)])
    n2_state.evaluate_board()
    assert n2_state.win_data["totalWin"] == 0


def test_trigger_once_and_full_replay_order(n2_state, monkeypatch):
    controlled_round(n2_state, monkeypatch)
    n2_state.run_spin(0)
    assert len(n2_events(n2_state)) == 1
    event = n2_events(n2_state)[0]
    assert event["progression"] >= 5
    assert n2_state.book.events[event["index"] - 1]["type"] == "levelUp"
    for e in n2_state.book.events[event["index"] + 1:]:
        if e["type"] == "tumbleBoard":
            assert e["stickyPositions"] == event["stickyPositions"]
    validate_book(n2_state.book.to_json(), n2_state.config)


def test_reset_next_bet_and_deterministic_rng(n2_state, monkeypatch):
    monkeypatch.setattr(n2_state, "draw_board", lambda: controlled_draw(n2_state))
    n2_state.run_spin(0, 42)
    expected = deepcopy(n2_state.book.to_json())
    assert len(n2_events(n2_state)) == 1
    n2_state.run_spin(1, 987)
    assert len(n2_events(n2_state)) == 1
    n2_state.run_spin(0, 42)
    assert n2_state.book.to_json() == expected
    def draw_quiet():
        n2_state.board = quiet_board(n2_state)
        n2_state.reel_positions = [0] * 6
        reveal_event(n2_state)
    monkeypatch.setattr(n2_state, "draw_board", draw_quiet)
    n2_state.run_spin(2)
    assert not n2_state.n2_wilds_activated and not n2_state.sticky_positions
    assert not any(s.name == "W" for col in n2_state.board for s in col)
    assert not n2_events(n2_state)
    assert n2_state.progression == 0 and n2_state.level == 1
    validate_book(n2_state.book.to_json(), n2_state.config)


@pytest.mark.parametrize("count", [0, 1, 3, 40])
def test_configurable_count_and_all_wild_board_has_no_own_pay(n2_state, count):
    n2_state.draw_board()
    n2_state.config.n2_wild_count = count
    n2_state.level, n2_state.progression = 2, 5
    n2_state.activate_n2_wilds()
    assert len(n2_state.sticky_positions) == min(count, 36)
    if count >= 36:
        n2_state.evaluate_board()
        assert n2_state.win_data["totalWin"] == 0


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "move_wild", "remove_wild", "wrong_source"])
def test_replay_rejects_invalid_n2(n2_state, monkeypatch, corruption):
    controlled_round(n2_state, monkeypatch)
    n2_state.run_spin(0)
    book = deepcopy(n2_state.book.to_json())
    sequence = book["events"]
    i = next(i for i, e in enumerate(sequence) if e["type"] == "wildAdded")
    if corruption == "missing":
        sequence.pop(i)
    elif corruption == "duplicate":
        sequence.insert(i, deepcopy(sequence[i]))
    elif corruption == "wrong_source":
        sequence[i]["positions"][0]["fromSymbol"] = "W"
    elif corruption == "move_wild":
        sequence[i]["stickyPositions"][0]["row"] = 20
    else:
        sequence[i]["board"][0][1] = {"name": "L1"}
    for j, event in enumerate(sequence):
        event["index"] = j
    with pytest.raises(AssertionError):
        validate_book(book, n2_state.config)


def test_wild_added_is_the_public_n2_event_not_legacy_armor(n2_state, monkeypatch):
    controlled_round(n2_state, monkeypatch)
    n2_state.run_spin(0)
    event_types = {e["type"] for e in n2_state.book.events}
    assert "wildAdded" in event_types
    assert "armorActivated" not in event_types


def test_n3_n4_n5_gameplay_effects_remain_disabled(n2_state, monkeypatch):
    """N2 is the only active Kitchen level feature in this milestone: crossing
    later thresholds must still only emit progression events, never a
    grid-changing effect (no N3/N4/N5 Kitchen feature exists yet)."""
    controlled_round(n2_state, monkeypatch)
    n2_state.config.progression_rules = ProgressionRules(thresholds=(1, 2, 3, 4))
    n2_state.run_spin(0)
    event_types = {e["type"] for e in n2_state.book.events}
    assert event_types & {"wildAdded"}
    assert not event_types & {"bossTriggered"}
    assert n2_state.level == 5
    validate_book(n2_state.book.to_json(), n2_state.config)
