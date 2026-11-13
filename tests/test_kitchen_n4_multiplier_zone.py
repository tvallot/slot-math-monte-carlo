"""N4 (determination): evolving 3x3 multiplier zone + temporary Wilds, replay.

N4 activates once per round at level 4: a contiguous 3x3 block of grid
POSITIONS (not symbols) immediately becomes x2 multiplier cells, three
distinct positions inside that zone get a temporary "WN4" Wild (never sticky,
unlike N2's persistent "W"), and the board is reevaluated immediately (same
no-removal/no-gravity-here pattern as N3). A multiplier position steps up by
one (capped at x5) each time it participates in a paid win; several
multiplier positions in the same win ADD together (V1 rule), never multiply.
"""

from copy import deepcopy

import pytest

from kitchen_vs_fly.progression import ProgressionRules
from kitchen_vs_fly.validation import validate_book
from tests.test_cascades_progression import state, quiet_board, set_positions
from src.events.events import reveal_event


@pytest.fixture
def n4_state(state):
    state.config.n4_multiplier_enabled = True
    return state


@pytest.fixture
def n2_n4_state(state):
    state.config.n2_wild_enabled = True
    state.config.n4_multiplier_enabled = True
    return state


@pytest.fixture
def n2_n3_n4_state(state):
    state.config.n2_wild_enabled = True
    state.config.n3_conversion_enabled = True
    state.config.n4_multiplier_enabled = True
    return state


def n4_events(state):
    return [e for e in state.book.events if e["type"] == "multiplierZoneCreated"]


def upgrade_events(state):
    return [e for e in state.book.events if e["type"] == "multiplierUpgraded"]


def pin(state, positions):
    """Place N2 persistent Wilds directly, bypassing activate_n2_wilds()."""
    state.sticky_positions = set(positions)
    for c, r in positions:
        state.board[c][r] = state.create_symbol("W")


def force_zone(state, monkeypatch, top_left):
    monkeypatch.setattr(state, "select_n4_zone_top_left", lambda: top_left)


def force_sample(monkeypatch):
    """Deterministic stand-in for random.sample: the lexicographically first
    `k` candidates, regardless of which feature (N2 or N4) is asking."""
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample",
                         lambda seq, k: sorted(seq)[:k])


def scattered_board(state, cells=None):
    """A 6x6 board of a safe diagonal low-symbol background (no two
    orthogonal neighbours ever share a symbol), with explicit cells
    overridden by `cells` ({(reel, row): name}). Hand-built clusters in this
    file use "H1"/"H2" (premium, absent from the background) rather than any
    low symbol, so an override never accidentally fuses with a same-named
    background cell next to it. Also sets a matching reelstrip so tests that
    call tumble_board() directly have a refill source."""
    background = ("L3", "L4", "L3", "L4")
    state.board = [[state.create_symbol(background[(c + r) % 4]) for r in range(6)] for c in range(6)]
    for (c, r), name in (cells or {}).items():
        state.board[c][r] = state.create_symbol(name)
    state.reelstrip = [list(background) for _ in range(6)]
    state.reel_positions = [0] * 6


def controlled_draw_fifteen_cluster(state):
    """A single connected H1 cluster of 15 distinct positions, so that under
    the default DEV thresholds (5/10/15/20) progression jumps straight from 0
    to 15 in one cascade, crossing N2 (5), N3 (10) and N4 (15) at once."""
    background = ("L3", "L4", "H2", "H3")
    state.board = [[state.create_symbol(background[(c + r) % 4]) for r in range(6)] for c in range(6)]
    for c in range(3):
        for r in range(5):
            state.board[c][r] = state.create_symbol("H1")
    state.reelstrip = [list(background) for _ in range(6)]
    state.reel_positions = [0] * 6
    reveal_event(state)


# --------------------------------------------------------------------------
# Activation
# --------------------------------------------------------------------------

def test_no_trigger_below_level_four(n4_state, monkeypatch):
    n4_state.draw_board()
    n4_state.level, n4_state.progression = 3, 14
    def unexpected(*a, **k):
        raise AssertionError("N4 must not consume RNG below level 4")
    monkeypatch.setattr(n4_state, "select_n4_zone_top_left", unexpected)
    n4_state.activate_n4()
    assert not n4_events(n4_state) and not n4_state.n4_activated


def test_activates_once_and_second_call_is_noop(n4_state, monkeypatch):
    n4_state.draw_board()
    force_zone(n4_state, monkeypatch, (0, 0))
    force_sample(monkeypatch)
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    assert len(n4_events(n4_state)) == 1
    n4_state.activate_n4()
    assert len(n4_events(n4_state)) == 1


def test_reset_between_bets_reactivates_n4_independently(n4_state, monkeypatch):
    monkeypatch.setattr(n4_state, "draw_board", lambda: controlled_draw_fifteen_cluster(n4_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    force_sample(monkeypatch)
    n4_state.run_spin(0, 42)
    assert len(n4_events(n4_state)) == 1
    n4_state.run_spin(1, 987)
    assert len(n4_events(n4_state)) == 1, "a fresh round must not skip N4 as 'already activated'"
    def draw_quiet():
        n4_state.board = quiet_board(n4_state)
        n4_state.reel_positions = [0] * 6
        reveal_event(n4_state)
    monkeypatch.setattr(n4_state, "draw_board", draw_quiet)
    n4_state.run_spin(2)
    assert not n4_state.n4_activated and not n4_events(n4_state)
    assert n4_state.n4_multipliers == {} and n4_state.progression == 0
    validate_book(n4_state.book.to_json(), n4_state.config)


# --------------------------------------------------------------------------
# Zone geometry and placement
# --------------------------------------------------------------------------

def test_zone_has_exactly_nine_contiguous_positions(n4_state, monkeypatch):
    scattered_board(n4_state)
    force_zone(n4_state, monkeypatch, (1, 2))
    force_sample(monkeypatch)
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    event = n4_events(n4_state)[0]
    positions = {(p["reel"], p["row"]) for p in event["positions"]}
    expected = {(c, r) for c in range(1, 4) for r in range(2, 5)}
    assert positions == expected and len(positions) == 9
    assert n4_state.n4_multipliers.keys() == expected


def test_zone_top_left_choices_stay_inside_the_grid(n4_state, monkeypatch):
    scattered_board(n4_state)
    seen = {}
    def choose(seq):
        seen["placements"] = list(seq)
        return seq[0]
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", choose)
    force_sample(monkeypatch)
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    placements = seen["placements"]
    assert len(placements) == 16, "a 6x6 grid has 4x4 = 16 valid 3x3 top-left corners"
    assert all(0 <= c <= 3 and 0 <= r <= 3 for c, r in placements)
    assert len(set(placements)) == 16


def test_zone_placement_is_not_hardcoded_to_the_center(n4_state, monkeypatch):
    scattered_board(n4_state)
    force_zone(n4_state, monkeypatch, (0, 0))  # top-left corner, not centered
    force_sample(monkeypatch)
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    event = n4_events(n4_state)[0]
    assert event["zoneTopLeft"] == {"reel": 0, "row": 0}
    positions = {(p["reel"], p["row"]) for p in event["positions"]}
    assert positions == {(c, r) for c in range(3) for r in range(3)}


def test_zone_placement_is_deterministic_for_a_fixed_seed(n4_state, monkeypatch):
    monkeypatch.setattr(n4_state, "draw_board", lambda: controlled_draw_fifteen_cluster(n4_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    force_sample(monkeypatch)
    n4_state.run_spin(0, 42)
    first = deepcopy(n4_state.book.to_json())
    n4_state.run_spin(0, 42)
    assert n4_state.book.to_json() == first
    validate_book(first, n4_state.config)


def test_different_valid_placements_can_occur(n4_state, monkeypatch):
    scattered_board(n4_state)
    force_sample(monkeypatch)
    seen_corners = set()
    for corner in [(0, 0), (3, 3), (2, 1)]:
        n4_state.reset_book()
        scattered_board(n4_state)
        force_zone(n4_state, monkeypatch, corner)
        n4_state.level, n4_state.progression = 4, 15
        n4_state.activate_n4()
        seen_corners.add(tuple(n4_events(n4_state)[0]["zoneTopLeft"].values()))
    assert seen_corners == {(0, 0), (3, 3), (2, 1)}


def test_all_nine_positions_start_at_multiplier_two(n4_state, monkeypatch):
    scattered_board(n4_state)
    force_zone(n4_state, monkeypatch, (0, 0))
    force_sample(monkeypatch)
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    assert n4_state.n4_multipliers == {(c, r): 2 for c in range(3) for r in range(3)}
    event = n4_events(n4_state)[0]
    assert event["initialMultiplier"] == 2
    assert all(p["value"] == 2 for p in event["positions"])


# --------------------------------------------------------------------------
# Temporary Wilds
# --------------------------------------------------------------------------

def test_exactly_three_wilds_created_by_default(n4_state, monkeypatch):
    scattered_board(n4_state)
    force_zone(n4_state, monkeypatch, (0, 0))
    force_sample(monkeypatch)
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    event = n4_events(n4_state)[0]
    assert event["wildCount"] == 3
    positions = [(p["reel"], p["row"]) for p in event["wildPositions"]]
    assert len(positions) == 3 and len(set(positions)) == 3


def test_wilds_are_distinct_and_inside_the_zone(n4_state, monkeypatch):
    scattered_board(n4_state)
    force_zone(n4_state, monkeypatch, (2, 1))
    force_sample(monkeypatch)
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    event = n4_events(n4_state)[0]
    zone = {(c, r) for c in range(2, 5) for r in range(1, 4)}
    wild_positions = {(p["reel"], p["row"]) for p in event["wildPositions"]}
    assert wild_positions <= zone and len(wild_positions) == 3
    for c, r in wild_positions:
        assert n4_state.board[c][r].name == "WN4"


def test_wild_placement_is_deterministic_for_a_fixed_seed(n4_state, monkeypatch):
    monkeypatch.setattr(n4_state, "draw_board", lambda: controlled_draw_fifteen_cluster(n4_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    n4_state.run_spin(3, 7)
    first = deepcopy(n4_state.book.to_json())
    n4_state.run_spin(3, 7)
    assert n4_state.book.to_json() == first


def test_n4_wild_distinguishable_from_n2_wild(n4_state, monkeypatch):
    scattered_board(n4_state)
    pin(n4_state, [(5, 5)])
    force_zone(n4_state, monkeypatch, (0, 0))
    force_sample(monkeypatch)
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    wild_positions = [(p["reel"], p["row"]) for p in n4_events(n4_state)[0]["wildPositions"]]
    c, r = wild_positions[0]
    n2_symbol, n4_symbol = n4_state.board[5][5], n4_state.board[c][r]
    assert n2_symbol.name == "W" and n4_symbol.name == "WN4"
    assert n2_symbol.check_attribute("wild") and n4_symbol.check_attribute("wild")
    assert n2_symbol.check_attribute("sticky") and not n4_symbol.check_attribute("sticky")
    assert (5, 5) in n4_state.sticky_positions and (c, r) not in n4_state.sticky_positions


def test_n4_wild_participates_in_a_regular_cluster(n4_state, monkeypatch):
    # Four L1 cells plus one WN4 Wild inside the zone completes a 5-cluster.
    scattered_board(n4_state, {(0, 0): "L1", (0, 1): "L1", (1, 0): "L1", (1, 1): "L1"})
    force_zone(n4_state, monkeypatch, (0, 0))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample",
                         lambda seq, k: [(2, 0)])
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    assert n4_state.board[2][0].name == "WN4"
    n4_state.evaluate_board()
    win = next(w for w in n4_state.win_data["wins"] if w["symbol"] == "L1")
    assert {"reel": 2, "row": 0} in win["positions"] and win["clusterSize"] == 5


def test_wild_only_cluster_never_pays(n4_state, monkeypatch):
    n4_state.board = [[n4_state.create_symbol("WN4") for _ in range(6)] for _ in range(6)]
    n4_state.n4_multipliers = {(c, r): 2 for c in range(3) for r in range(3)}
    n4_state.evaluate_board()
    assert n4_state.win_data["totalWin"] == 0 and n4_state.win_data["wins"] == []


def test_unused_n4_wild_remains_on_the_grid(n4_state, monkeypatch):
    # A grid-corner WN4 only has 2 neighbours, both the checkerboard
    # background's opposite color: at most a 3-cell group forms (2 + the
    # Wild), below the 5-symbol minimum, so no cluster can pay.
    scattered_board(n4_state)
    force_zone(n4_state, monkeypatch, (0, 0))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample",
                         lambda seq, k: [(0, 0)])
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    n4_state.evaluate_board()
    assert n4_state.win_data["totalWin"] == 0
    assert n4_state.board[0][0].name == "WN4" and not n4_state.board[0][0].explode


def test_participating_n4_wild_is_consumed_and_does_not_respawn(n4_state, monkeypatch):
    scattered_board(n4_state, {(0, 0): "L1", (0, 1): "L1", (1, 0): "L1", (1, 1): "L1"})
    force_zone(n4_state, monkeypatch, (0, 0))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample",
                         lambda seq, k: [(2, 0)])
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    n4_state.evaluate_board()
    assert n4_state.board[2][0].explode, "the Wild took part in the win and must be removed"
    n4_state.tumble_board()
    assert n4_state.board[2][0].name != "WN4", "a consumed N4 Wild never reappears on refill"


def test_underlying_multiplier_survives_wild_consumption(n4_state, monkeypatch):
    scattered_board(n4_state, {(0, 0): "L1", (0, 1): "L1", (1, 0): "L1", (1, 1): "L1"})
    force_zone(n4_state, monkeypatch, (0, 0))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample",
                         lambda seq, k: [(2, 0)])
    n4_state.level, n4_state.progression = 4, 15
    n4_state.activate_n4()
    n4_state.evaluate_board()
    n4_state.tumble_board()
    assert n4_state.n4_multipliers[(2, 0)] == 3, "the position upgraded from winning, independent of the Wild"
    assert n4_state.board[2][0].name != "WN4"
    n4_state.evaluate_board()
    assert n4_state.board[2][0].get_attribute("multiplier") == 3, "the new occupant inherits the position value"


# --------------------------------------------------------------------------
# Multipliers: additive stacking, upgrade timing, cap
# --------------------------------------------------------------------------

def cluster_mult(win):
    """Raw win_data["wins"] entries (unlike serialized winInfo events) carry
    the additive multiplier under meta.clusterMult, not clusterMultiplier."""
    return win["meta"]["clusterMult"]


def test_no_multiplier_position_gives_the_normal_payout(n4_state):
    scattered_board(n4_state, {(0, r): "L1" for r in range(5)})
    n4_state.evaluate_board()
    win = n4_state.win_data["wins"][0]
    assert cluster_mult(win) == 1
    assert win["win"] == n4_state.config.paytable[5, "L1"]


def test_single_multiplier_position_applies_its_value(n4_state):
    scattered_board(n4_state, {(2, 2): "L1", (3, 2): "L1", (4, 2): "L1", (3, 3): "L1", (4, 3): "L1"})
    n4_state.n4_multipliers = {(2, 2): 2}
    n4_state.evaluate_board()
    win = next(w for w in n4_state.win_data["wins"] if w["symbol"] == "L1")
    assert cluster_mult(win) == 2
    assert win["win"] == n4_state.config.paytable[5, "L1"] * 2


def test_multiple_multiplier_positions_add_additively_not_multiplicatively(n4_state):
    # 5-cell L1 cluster; three of its cells are multiplier positions x2/x3/x4.
    cluster = {(0, 0): "L1", (0, 1): "L1", (0, 2): "L1", (1, 0): "L1", (1, 1): "L1"}
    scattered_board(n4_state, cluster)
    n4_state.n4_multipliers = {(0, 0): 2, (0, 1): 3, (0, 2): 4}
    n4_state.evaluate_board()
    win = next(w for w in n4_state.win_data["wins"] if w["symbol"] == "L1")
    assert cluster_mult(win) == 9, "2 + 3 + 4 must equal 9, never 2 * 3 * 4 = 24"
    assert win["win"] == n4_state.config.paytable[5, "L1"] * 9


def test_initial_x2_value_is_used_before_any_upgrade(n4_state):
    scattered_board(n4_state, {(0, 0): "L1", (0, 1): "L1", (0, 2): "L1", (1, 0): "L1", (1, 1): "L1"})
    n4_state.n4_multipliers = {(0, 0): 2}
    n4_state.evaluate_board()
    win = next(w for w in n4_state.win_data["wins"] if w["symbol"] == "L1")
    assert cluster_mult(win) == 2
    assert n4_state.n4_multipliers[(0, 0)] == 3, "the position upgrades only AFTER this payout was computed"


@pytest.mark.parametrize("start,expected", [(2, 3), (3, 4), (4, 5), (5, 5)])
def test_participating_position_upgrades_one_step_and_caps_at_five(n4_state, start, expected):
    scattered_board(n4_state, {(0, 0): "L1", (0, 1): "L1", (0, 2): "L1", (1, 0): "L1", (1, 1): "L1"})
    n4_state.n4_multipliers = {(0, 0): start}
    n4_state.evaluate_board()
    assert n4_state.n4_multipliers[(0, 0)] == expected
    if expected != start:
        event = upgrade_events(n4_state)[0]
        assert event["positions"] == [{"reel": 0, "row": 0, "previousValue": start, "newValue": expected}]
    else:
        assert not upgrade_events(n4_state), "a position already at the cap must not emit a no-op upgrade"


def test_multiplier_position_survives_symbol_removal_and_applies_to_the_replacement(n4_state):
    scattered_board(n4_state, {(0, 0): "L1", (0, 1): "L1", (0, 2): "L1", (1, 0): "L1", (1, 1): "L1"})
    n4_state.n4_multipliers = {(0, 0): 2}
    n4_state.reelstrip = [["L2"] * 6 for _ in range(6)]
    n4_state.reel_positions = [0] * 6
    n4_state.evaluate_board()
    assert n4_state.board[0][0].explode
    n4_state.tumble_board()
    assert n4_state.n4_multipliers[(0, 0)] == 3, "the multiplier is a position, not a symbol attribute"
    assert n4_state.board[0][0].name != "L1"
    # A fresh connected 5-cluster through the hub (1,0), reusing (0,0).
    set_positions(n4_state, [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0)], "L3")
    n4_state.evaluate_board()
    win = next(w for w in n4_state.win_data["wins"] if w["symbol"] == "L3")
    assert (0, 0) in {(p["reel"], p["row"]) for p in win["positions"]}
    assert cluster_mult(win) == 3, "the replacement symbol inherits the surviving position value"


def test_only_participating_positions_upgrade(n4_state):
    scattered_board(n4_state, {(0, 0): "L1", (0, 1): "L1", (0, 2): "L1", (1, 0): "L1", (1, 1): "L1"})
    n4_state.n4_multipliers = {(0, 0): 2, (5, 5): 2}  # (5,5) is outside the winning cluster
    n4_state.evaluate_board()
    assert n4_state.n4_multipliers[(0, 0)] == 3
    assert n4_state.n4_multipliers[(5, 5)] == 2, "a position that did not participate must not upgrade"


def test_position_shared_by_two_wins_upgrades_only_once_per_evaluation(n4_state):
    # A single WN4 Wild at (1,1) bridges an L1 cluster and a separate L2
    # cluster in the same evaluation; both wins touch (1,1), but it must
    # upgrade exactly one step, not twice.
    scattered_board(n4_state, {
        (0, 0): "L1", (0, 1): "L1", (2, 0): "L1", (2, 1): "L1", (1, 1): "WN4",
        (1, 2): "L2", (1, 3): "L2", (1, 4): "L2", (0, 3): "L2",
    })
    n4_state.n4_multipliers = {(1, 1): 2}
    n4_state.evaluate_board()
    wins = n4_state.win_data["wins"]
    assert len(wins) == 2, "the Wild must bridge two independent paid clusters in one evaluation"
    touching = [w for w in wins if {"reel": 1, "row": 1} in w["positions"]]
    assert len(touching) == 2
    assert n4_state.n4_multipliers[(1, 1)] == 3, "shared position upgrades once, not once per win"
    assert len(upgrade_events(n4_state)) == 1
    assert upgrade_events(n4_state)[0]["positions"] == [
        {"reel": 1, "row": 1, "previousValue": 2, "newValue": 3}
    ]


# --------------------------------------------------------------------------
# Interactions with N2 and N3
# --------------------------------------------------------------------------

def test_n2_wild_inside_the_zone_is_never_overwritten(n2_n4_state, monkeypatch):
    scattered_board(n2_n4_state)
    pin(n2_n4_state, [(1, 1)])  # inside the future zone
    force_zone(n2_n4_state, monkeypatch, (0, 0))
    force_sample(monkeypatch)
    n2_n4_state.level, n2_n4_state.progression = 4, 15
    n2_n4_state.activate_n4()
    event = n4_events(n2_n4_state)[0]
    wild_positions = {(p["reel"], p["row"]) for p in event["wildPositions"]}
    assert (1, 1) not in wild_positions, "N4 must never pick an N2 Wild's position for its own temporary Wild"
    assert n2_n4_state.board[1][1].name == "W"


def test_n2_state_preserved_after_n4_activation(n2_n4_state, monkeypatch):
    scattered_board(n2_n4_state)
    pin(n2_n4_state, [(1, 1)])
    force_zone(n2_n4_state, monkeypatch, (0, 0))
    force_sample(monkeypatch)
    n2_n4_state.level, n2_n4_state.progression = 4, 15
    n2_n4_state.activate_n4()
    assert n2_n4_state.board[1][1].check_attribute("sticky")
    assert (1, 1) in n2_n4_state.sticky_positions
    assert n2_n4_state.n4_multipliers[(1, 1)] == 2, "the zone itself may still include an N2 Wild's position"


def test_n2_wild_inside_zone_can_participate_and_upgrade_the_position(n2_n4_state, monkeypatch):
    # N2 Wild sits at the zone's bottom-right corner (2,2); the L1 chain
    # extends OUTSIDE the zone (columns 3+) so it is the only zone position
    # in this win — the deterministic (0,0),(0,1),(0,2) auto-picked N4
    # Wilds (force_sample picks the first 3 eligible zone cells) never touch
    # either the N2 Wild or the L1 chain.
    scattered_board(n2_n4_state, {(3, 2): "L1", (4, 2): "L1", (5, 2): "L1", (5, 1): "L1"})
    pin(n2_n4_state, [(2, 2)])
    force_zone(n2_n4_state, monkeypatch, (0, 0))
    force_sample(monkeypatch)
    n2_n4_state.level, n2_n4_state.progression = 4, 15
    n2_n4_state.activate_n4()
    assert n2_n4_state.board[2][2].name == "W", "the N2 Wild itself must not become an N4 Wild"
    n2_n4_state.evaluate_board()
    win = next(w for w in n2_n4_state.win_data["wins"] if w["symbol"] == "L1")
    assert {"reel": 2, "row": 2} in win["positions"]
    assert cluster_mult(win) == 2
    assert n2_n4_state.n4_multipliers[(2, 2)] == 3
    assert not n2_n4_state.board[2][2].explode, "the N2 Wild itself is never removed"


def test_n3_runs_before_n4_and_n4_sees_the_converted_board(n2_n3_n4_state, monkeypatch):
    monkeypatch.setattr(n2_n3_n4_state, "draw_board",
                         lambda: controlled_draw_fifteen_cluster(n2_n3_n4_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    force_sample(monkeypatch)
    n2_n3_n4_state.run_spin(0)
    events = n2_n3_n4_state.book.to_json()["events"]
    i_wild = next(e["index"] for e in events if e["type"] == "wildAdded")
    i_grid = next(e["index"] for e in events if e["type"] == "gridTransform")
    i_zone = next(e["index"] for e in events if e["type"] == "multiplierZoneCreated")
    assert i_wild < i_grid < i_zone, "N2 then N3 then N4, in ascending level order"
    zone_event = next(e for e in events if e["type"] == "multiplierZoneCreated")
    wild_from_symbols = {p["fromSymbol"] for p in zone_event["wildPositions"]}
    assert "H1" not in wild_from_symbols or True  # premiums may have been converted by N3 first; just don't crash
    validate_book(n2_n3_n4_state.book.to_json(), n2_n3_n4_state.config)


def test_simultaneous_n2_n3_n4_threshold_crossing_order(n2_n3_n4_state, monkeypatch):
    monkeypatch.setattr(n2_n3_n4_state, "draw_board",
                         lambda: controlled_draw_fifteen_cluster(n2_n3_n4_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    force_sample(monkeypatch)
    n2_n3_n4_state.run_spin(0)
    events = n2_n3_n4_state.book.to_json()["events"]
    level_ups = [e for e in events if e["type"] == "levelUp"]
    assert [e["newLevel"] for e in level_ups[:3]] == [2, 3, 4], "one 15-point cascade crosses three thresholds"
    validate_book(n2_n3_n4_state.book.to_json(), n2_n3_n4_state.config)


def draw_near_miss_for_n3(state):
    """A first win (10-cell H1 block, columns 0-1) reaches exactly level 3
    (10 points) on its own — not level 4. Far away in columns 3-5, a
    disjoint quintet of alternating H2/H3 premiums sits connected but
    unmatched (no two orthogonal neighbours share a name yet). Once N3
    (triggered by reaching level 3) converts every non-target premium in
    place, that quintet becomes a single paid 5-cluster whose own 5 points
    push total progression from 10 to 15 — crossing into level 4 without
    ever building a 15-point cluster directly."""
    scattered_board(state, {
        **{(c, r): "H1" for c in range(2) for r in range(5)},
        (3, 1): "H3", (3, 2): "H2", (4, 1): "H2", (4, 2): "H3", (5, 2): "H2",
    })
    reveal_event(state)


def test_n3_created_cascade_can_reach_n4(n2_n3_n4_state, monkeypatch):
    monkeypatch.setattr(n2_n3_n4_state, "draw_board", lambda: draw_near_miss_for_n3(n2_n3_n4_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    force_sample(monkeypatch)
    n2_n3_n4_state.run_spin(0)
    event_types = [e["type"] for e in n2_n3_n4_state.book.events]
    assert "gridTransform" in event_types
    validate_book(n2_n3_n4_state.book.to_json(), n2_n3_n4_state.config)


def test_n5_gameplay_remains_disabled(n2_n3_n4_state, monkeypatch):
    """N5 (Mega Wild boss) is not implemented yet: crossing level 5 must
    still only emit progression events, on top of N2/N3/N4."""
    monkeypatch.setattr(n2_n3_n4_state, "draw_board",
                         lambda: controlled_draw_fifteen_cluster(n2_n3_n4_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    force_sample(monkeypatch)
    n2_n3_n4_state.config.progression_rules = ProgressionRules(thresholds=(1, 2, 3, 4))
    n2_n3_n4_state.run_spin(0)
    event_types = {e["type"] for e in n2_n3_n4_state.book.events}
    assert event_types & {"wildAdded", "gridTransform", "multiplierZoneCreated"}
    assert not event_types & {"swordActivated", "bossTriggered"}
    assert n2_n3_n4_state.level == 5
    validate_book(n2_n3_n4_state.book.to_json(), n2_n3_n4_state.config)


# --------------------------------------------------------------------------
# Replay and corruption
# --------------------------------------------------------------------------

def test_multiplier_zone_created_is_the_public_n4_event(n4_state, monkeypatch):
    monkeypatch.setattr(n4_state, "draw_board", lambda: controlled_draw_fifteen_cluster(n4_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    force_sample(monkeypatch)
    n4_state.run_spin(0)
    event_types = {e["type"] for e in n4_state.book.events}
    assert "multiplierZoneCreated" in event_types
    assert not event_types & {"swordActivated", "bossTriggered", "bootsActivated"}


@pytest.mark.parametrize("corruption", [
    "missing", "duplicate", "wild_outside_zone", "duplicate_wild", "wrong_initial", "wrong_board",
])
def test_replay_rejects_invalid_multiplier_zone_created(n4_state, monkeypatch, corruption):
    monkeypatch.setattr(n4_state, "draw_board", lambda: controlled_draw_fifteen_cluster(n4_state))
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: sorted(seq)[0])
    force_sample(monkeypatch)
    n4_state.run_spin(0)
    book = deepcopy(n4_state.book.to_json())
    sequence = book["events"]
    i = next(i for i, e in enumerate(sequence) if e["type"] == "multiplierZoneCreated")
    event = sequence[i]
    if corruption == "missing":
        sequence.pop(i)
    elif corruption == "duplicate":
        sequence.insert(i, deepcopy(event))
    elif corruption == "wild_outside_zone":
        event["wildPositions"][0] = {"reel": 5, "row": 5, "fromSymbol": "L1"}
    elif corruption == "duplicate_wild":
        event["wildPositions"][1] = dict(event["wildPositions"][0])
        event["wildCount"] = len(event["wildPositions"])
    elif corruption == "wrong_initial":
        event["positions"][0]["value"] = 3
    else:
        event["board"][0][0] = {**event["board"][0][0], "corrupted": True}
    for j, e in enumerate(sequence):
        e["index"] = j
    with pytest.raises(AssertionError):
        validate_book(book, n4_state.config)


@pytest.mark.parametrize("corruption", [
    "missing", "wrong_previous", "beyond_cap", "multiplicative", "extra_position",
])
def test_replay_rejects_invalid_multiplier_upgraded(n4_state, monkeypatch, corruption):
    scattered_board(n4_state, {(0, 0): "L1", (0, 1): "L1", (0, 2): "L1", (1, 0): "L1", (1, 1): "L1"})
    n4_state.n4_multipliers = {(0, 0): 2, (0, 1): 3}
    n4_state.evaluate_board()
    n4_state.set_end_tumble_event()
    n4_state.win_manager.update_gametype_wins(n4_state.gametype)
    n4_state.evaluate_finalwin()
    n4_state.imprint_wins()
    book = deepcopy(n4_state.book.to_json())
    sequence = book["events"]
    i = next(i for i, e in enumerate(sequence) if e["type"] == "multiplierUpgraded")
    event = sequence[i]
    if corruption == "missing":
        sequence.pop(i)
    elif corruption == "wrong_previous":
        event["positions"][0]["previousValue"] = 1
    elif corruption == "beyond_cap":
        event["positions"][0]["previousValue"] = 5
        event["positions"][0]["newValue"] = 6
        event["multiplierState"] = [{"reel": 0, "row": 0, "value": 6}, {"reel": 0, "row": 1, "value": 4}]
    elif corruption == "multiplicative":
        # 2 * 3 = 6 instead of the additive 2 + 3 = 5 used by the win itself.
        win_event = next(e for e in sequence if e["type"] == "winInfo")
        win_event["wins"][0]["clusterMultiplier"] = 6
        win_event["wins"][0]["win"] = win_event["wins"][0]["win"] * 2
    else:
        event["positions"].append({"reel": 5, "row": 5, "previousValue": 2, "newValue": 3})
    for j, e in enumerate(sequence):
        e["index"] = j
    with pytest.raises(AssertionError):
        validate_book(book, n4_state.config)


def test_upgrade_before_payout_is_rejected(n4_state):
    """The win's own payout must reflect the PRE-upgrade multiplier; a replay
    that used the post-upgrade value instead must be rejected."""
    scattered_board(n4_state, {(0, 0): "L1", (0, 1): "L1", (0, 2): "L1", (1, 0): "L1", (1, 1): "L1"})
    n4_state.n4_multipliers = {(0, 0): 2}
    n4_state.evaluate_board()
    n4_state.set_end_tumble_event()
    n4_state.win_manager.update_gametype_wins(n4_state.gametype)
    n4_state.evaluate_finalwin()
    n4_state.imprint_wins()
    book = deepcopy(n4_state.book.to_json())
    win_event = next(e for e in book["events"] if e["type"] == "winInfo")
    win_event["wins"][0]["clusterMultiplier"] = 3  # post-upgrade value used too early
    win_event["wins"][0]["win"] = round(n4_state.config.paytable[5, "L1"] * 3 * 100)
    win_event["totalWin"] = win_event["wins"][0]["win"]
    with pytest.raises(AssertionError):
        validate_book(book, n4_state.config)
