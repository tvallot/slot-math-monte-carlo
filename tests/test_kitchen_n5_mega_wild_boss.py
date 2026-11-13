"""N5 (boss): Mega Wild fly, 3x3 -> 2x2 -> 1x1 -> defeated, replay.

N5 activates once per round at level 5: a single multi-cell Mega Wild entity
(footprint tracked in self.n5_boss_positions, symbol name "WN5") enters the
grid in a 3x3 form that never overlaps an N2 persistent Wild and, whenever
geometrically possible, never overlaps the N4 multiplier zone either (see
select_n5_initial_top_left in gamestate.py for the documented minimal-overlap
fallback for the 4/16 N4 placements where no zero-overlap 3x3 exists). Each
boss size gets exactly one dedicated board evaluation (enforced by the
internal self.n5_boss_awaiting_evaluation flag); a hit shrinks and relocates
the footprint (mandatory anchor movement, N2-safe, N4 overlap now allowed),
and a hit at 1x1 defeats the boss. The Mega Wild is never itself a payable
symbol; it only substitutes for a regular symbol's cluster.
"""

from copy import deepcopy

import pytest

from kitchen_vs_fly.progression import ProgressionRules
from kitchen_vs_fly.validation import validate_book
from tests.test_cascades_progression import state, quiet_board, set_positions
from tests.test_kitchen_n4_multiplier_zone import pin, scattered_board, force_sample, force_zone


@pytest.fixture
def n5_state(state):
    state.config.n5_boss_enabled = True
    return state


@pytest.fixture
def n2_n5_state(state):
    state.config.n2_wild_enabled = True
    state.config.n5_boss_enabled = True
    return state


@pytest.fixture
def n4_n5_state(state):
    state.config.n4_multiplier_enabled = True
    state.config.n5_boss_enabled = True
    return state


@pytest.fixture
def n2_n4_n5_state(state):
    state.config.n2_wild_enabled = True
    state.config.n4_multiplier_enabled = True
    state.config.n5_boss_enabled = True
    return state


def triggered_events(state):
    return [e for e in state.book.events if e["type"] == "bossTriggered"]


def shrunk_events(state):
    return [e for e in state.book.events if e["type"] == "bossShrunk"]


def defeated_events(state):
    return [e for e in state.book.events if e["type"] == "bossDefeated"]


def force_initial(state, monkeypatch, top_left):
    monkeypatch.setattr(state, "select_n5_initial_top_left", lambda: top_left)


def force_relocation(state, monkeypatch, mapping):
    """`mapping`: {size: top_left} for the 2x2 and/or 1x1 relocation steps."""
    monkeypatch.setattr(state, "select_n5_relocation_top_left",
                         lambda size, previous_anchor: mapping[size])


def ready_board(state, cells=None):
    """A safe low-symbol background plus a matching reelstrip, ready for
    direct activate_n5_boss()/evaluate_board()/tumble_board() calls without
    going through run_spin()."""
    scattered_board(state, cells)
    return state


def set_cluster(state, positions, symbol="H1"):
    set_positions(state, positions, symbol)


# --------------------------------------------------------------------------
# Activation
# --------------------------------------------------------------------------

def test_no_trigger_below_level_five(n5_state, monkeypatch):
    ready_board(n5_state)
    n5_state.level, n5_state.progression = 4, 19
    def unexpected():
        raise AssertionError("N5 must not consume RNG below level 5")
    monkeypatch.setattr(n5_state, "select_n5_initial_top_left", unexpected)
    n5_state.activate_n5_boss()
    assert not n5_state.n5_boss_active and not triggered_events(n5_state)


def test_activates_once_and_second_call_is_noop(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    assert len(triggered_events(n5_state)) == 1
    n5_state.activate_n5_boss()
    assert len(triggered_events(n5_state)) == 1


def test_reset_between_bets_reactivates_n5_independently(n5_state, monkeypatch):
    def draw():
        ready_board(n5_state)
    monkeypatch.setattr(n5_state, "draw_board", draw)
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.run_spin(0, 42)
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    assert n5_state.n5_boss_active
    n5_state.reset_book()
    assert not n5_state.n5_boss_active and not n5_state.n5_boss_defeated
    assert n5_state.n5_boss_positions == set() and n5_state.n5_boss_size == 0
    assert n5_state.n5_boss_anchor is None


# --------------------------------------------------------------------------
# Initial 3x3 placement
# --------------------------------------------------------------------------

def test_initial_footprint_is_nine_contiguous_positions(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (1, 2))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    assert n5_state.n5_boss_positions == {(c, r) for c in range(1, 4) for r in range(2, 5)}
    assert n5_state.n5_boss_size == 3 and n5_state.n5_boss_anchor == (1, 2)
    event = triggered_events(n5_state)[0]
    assert {(p["reel"], p["row"]) for p in event["footprint"]} == n5_state.n5_boss_positions
    assert event["size"] == 3 and event["anchor"] == {"reel": 1, "row": 2}


def test_all_sixteen_placements_stay_inside_the_grid():
    from kitchen_vs_fly.game_config import GameConfig
    from kitchen_vs_fly.gamestate import GameState
    state_ = GameState(GameConfig())
    candidates = state_.n5_boss_candidate_placements(3)
    assert candidates == sorted((c, r) for c in range(4) for r in range(4))


def test_deterministic_placement_for_a_fixed_seed(n5_state, monkeypatch):
    def draw():
        ready_board(n5_state)
        set_cluster(n5_state, [(c, 0) for c in range(5)])
    monkeypatch.setattr(n5_state, "draw_board", draw)
    n5_state.config.progression_rules = ProgressionRules(thresholds=(1, 2, 3, 4))
    n5_state.run_spin(0, 123)
    first = deepcopy(n5_state.book.to_json())
    def draw2():
        ready_board(n5_state)
        set_cluster(n5_state, [(c, 0) for c in range(5)])
    monkeypatch.setattr(n5_state, "draw_board", draw2)
    n5_state.run_spin(0, 123)
    second = n5_state.book.to_json()
    assert first == second


def test_no_overlap_with_n4_zone_when_avoidable(n4_n5_state, monkeypatch):
    ready_board(n4_n5_state)
    force_zone(n4_n5_state, monkeypatch, (0, 0))  # N4 zone at (0,0)-(2,2)
    force_sample(monkeypatch)
    # select_n5_initial_top_left is left unpatched: a zero-overlap 3x3
    # candidate exists here, so the real selection logic must find it.
    n4_n5_state.level, n4_n5_state.progression = 4, 15
    n4_n5_state.activate_n4()
    n4_n5_state.level, n4_n5_state.progression = 5, 20
    n4_n5_state.activate_n5_boss()
    zone = set(n4_n5_state.n4_multipliers)
    assert n4_n5_state.n5_boss_positions.isdisjoint(zone)


def test_all_n4_placements_permit_a_valid_or_minimal_overlap_boss_placement():
    """Exhaustive proof (see GAME_DESIGN.md and
    docs/milestones/V0.3.5_kitchen_n5_mega_wild_fly.md): for every one of the
    16 possible N4 3x3 top-left corners on this 6x6 grid, either a
    zero-overlap 3x3 boss candidate exists, or (for the four fully interior
    N4 corners) the minimum achievable overlap is exactly 1 cell -- never
    the full zone, and never left unresolved."""
    from kitchen_vs_fly.game_config import GameConfig
    from kitchen_vs_fly.gamestate import GameState
    config = GameConfig()
    state_ = GameState(config)
    size = config.n5_boss_size
    interior = {(1, 1), (1, 2), (2, 1), (2, 2)}
    for a in range(4):
        for b in range(4):
            n4_zone = {(c, r) for c in range(a, a + 3) for r in range(b, b + 3)}
            candidates = state_.n5_boss_candidate_placements(size)
            overlaps = {
                p: len({(c, r) for c in range(p[0], p[0] + size) for r in range(p[1], p[1] + size)} & n4_zone)
                for p in candidates
            }
            best = min(overlaps.values())
            if (a, b) in interior:
                assert best == 1, f"N4 at {(a, b)}: expected minimum overlap of 1, got {best}"
            else:
                assert best == 0, f"N4 at {(a, b)}: expected a zero-overlap placement, got {best}"


def test_initial_placement_never_overlaps_an_n2_wild(n2_n5_state, monkeypatch):
    ready_board(n2_n5_state)
    pin(n2_n5_state, [(2, 2)])
    n2_n5_state.level, n2_n5_state.progression = 5, 20
    n2_n5_state.activate_n5_boss()
    assert (2, 2) not in n2_n5_state.n5_boss_positions


def test_multiple_distinct_initial_placements_observable(n5_state, monkeypatch):
    seen = set()
    for seed in range(30):
        def draw():
            ready_board(n5_state)
        monkeypatch.setattr(n5_state, "draw_board", draw)
        n5_state.run_spin(seed, seed)
        n5_state.level, n5_state.progression = 5, 20
        n5_state.activate_n5_boss()
        seen.add(n5_state.n5_boss_anchor)
        n5_state.reset_book()
    assert len(seen) > 1


# --------------------------------------------------------------------------
# Mega Wild Wild semantics
# --------------------------------------------------------------------------

def test_boss_substitutes_for_a_regular_symbol(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))  # footprint: cols/rows 0-2
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    # A plain 5-cell H1 cluster, entirely outside and non-adjacent to the
    # boss footprint: an ordinary win the boss has nothing to do with.
    set_cluster(n5_state, [(3, 3), (4, 3), (4, 4), (5, 3), (5, 4)], "H1")
    n5_state.evaluate_board()
    wins = {w["symbol"]: w["clusterSize"] for w in n5_state.win_data["wins"]}
    assert wins.get("H1") == 5


def test_boss_bridges_a_cluster_through_its_footprint(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (2, 2))  # footprint reels/rows 2-4
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    # Two separate H1 groups only connected through the boss's own footprint.
    set_cluster(n5_state, [(0, 2), (0, 3), (1, 2)], "H1")
    set_cluster(n5_state, [(5, 2), (5, 3), (4, 2)], "H1")
    n5_state.evaluate_board()
    h1_wins = [w for w in n5_state.win_data["wins"] if w["symbol"] == "H1"]
    assert len(h1_wins) == 1
    assert h1_wins[0]["clusterSize"] >= 9  # 3 + 3 + at least 3 bridging boss cells


def test_boss_only_cluster_never_pays(n5_state, monkeypatch):
    # A single touching real symbol would already pay via the 9-cell Wild
    # bulk alone (9 + 1 >= min_cluster_size); the only way to isolate "the
    # Mega Wild forms no cluster by itself" is to remove every real symbol
    # from the board entirely, mirroring test_wild_only_board_does_not_pay
    # in test_kitchen_n2_persistent_wilds.py.
    n5_state.board = [[n5_state.create_symbol("WN5") for _ in range(6)] for _ in range(6)]
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.n5_boss_active = True  # placement already "done" for this check
    n5_state.n5_boss_positions = {(c, r) for c in range(6) for r in range(6)}
    n5_state.evaluate_board()
    assert n5_state.win_data["totalWin"] == 0 and not n5_state.win_data["wins"]


def test_boss_plus_n2_plus_n4_wild_only_cluster_never_pays(n2_n4_n5_state, monkeypatch):
    # Whole board covered by a mix of the three Wild kinds, no real symbol
    # anywhere: no cluster can ever be seeded (see test_boss_only_cluster_never_pays).
    n2_n4_n5_state.board = [[n2_n4_n5_state.create_symbol("L1") for _ in range(6)] for _ in range(6)]
    pin(n2_n4_n5_state, [(0, 0), (0, 1)])
    n2_n4_n5_state.n4_multipliers = {(1, 0): 2, (1, 1): 2}
    n2_n4_n5_state.board[1][0] = n2_n4_n5_state.create_symbol("WN4")
    n2_n4_n5_state.board[1][1] = n2_n4_n5_state.create_symbol("WN4")
    boss_positions = {(c, r) for c in range(6) for r in range(6)} - {(0, 0), (0, 1), (1, 0), (1, 1)}
    for c, r in boss_positions:
        n2_n4_n5_state.board[c][r] = n2_n4_n5_state.create_symbol("WN5")
    n2_n4_n5_state.n5_boss_active = True
    n2_n4_n5_state.n5_boss_positions = boss_positions
    n2_n4_n5_state.evaluate_board()
    assert n2_n4_n5_state.win_data["totalWin"] == 0


# --------------------------------------------------------------------------
# Hits
# --------------------------------------------------------------------------

def test_participating_boss_registers_exactly_one_hit(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    force_relocation(n5_state, monkeypatch, {2: (3, 3)})
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    set_cluster(n5_state, [(0, 3), (1, 3), (2, 3), (3, 3)], "H1")  # touches (0,0) via boss at (0,0)? no
    # Build a cluster that actually touches the boss footprint (0,0)-(2,2).
    set_cluster(n5_state, [(0, 3), (0, 4), (1, 4), (2, 4), (2, 3)], "H1")
    n5_state.board[0][2] = n5_state.create_symbol("H1")  # adjacent to boss cell (0,2)... but boss occupies it
    n5_state.evaluate_board()
    assert n5_state.n5_boss_advance_pending
    assert not n5_state.n5_boss_awaiting_evaluation


def test_several_boss_cells_in_one_cluster_still_one_hit(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    set_cluster(n5_state, [(0, 3), (1, 3), (2, 3), (0, 4), (1, 4)], "H1")
    n5_state.evaluate_board()
    h1 = [w for w in n5_state.win_data["wins"] if w["symbol"] == "H1"][0]
    boss_cells_in_win = {(p["reel"], p["row"]) for p in h1["positions"]} & n5_state.n5_boss_positions
    assert len(boss_cells_in_win) >= 1
    assert n5_state.n5_boss_advance_pending


def test_multiple_clusters_sharing_boss_still_one_hit(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (2, 2))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    set_cluster(n5_state, [(0, 2), (0, 3), (1, 2), (1, 3)], "H1")
    set_cluster(n5_state, [(5, 2), (5, 3), (4, 2), (4, 3)], "H2")
    n5_state.evaluate_board()
    symbols_hit = {w["symbol"] for w in n5_state.win_data["wins"]}
    assert {"H1", "H2"} <= symbols_hit
    assert n5_state.n5_boss_advance_pending  # exactly one hit is a boolean flag, never a count


def test_non_participating_boss_still_advances(n5_state, monkeypatch):
    # V0.3.21: the boss does not need a winning cluster overlapping its
    # footprint. The size's dedicated evaluation is consumed whether or not
    # anything paid, and the boss is then due to advance.
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    n5_state.win_data = {"totalWin": 1.0, "wins": [
        {"symbol": "H1", "positions": [{"reel": 3, "row": 3}, {"reel": 3, "row": 4},
                                        {"reel": 4, "row": 3}, {"reel": 4, "row": 4}, {"reel": 5, "row": 5}]},
    ]}
    n5_state.complete_n5_boss_evaluation()
    assert n5_state.n5_boss_advance_pending
    assert not n5_state.n5_boss_awaiting_evaluation
    assert n5_state.n5_boss_size == 3  # still 3x3 until activate_n5_boss runs


def test_zero_win_evaluation_still_advances(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    force_relocation(n5_state, monkeypatch, {2: (3, 3)})
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    n5_state.win_data = {"totalWin": 0, "wins": []}
    n5_state.complete_n5_boss_evaluation()
    assert n5_state.n5_boss_advance_pending
    n5_state.activate_n5_boss()
    assert n5_state.n5_boss_size == 2


def test_second_evaluation_at_same_size_does_not_advance_again(n5_state, monkeypatch):
    """One dedicated evaluation per size: a later evaluation of the same
    (unchanged) footprint must not count again once advance is pending/used."""
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    n5_state.complete_n5_boss_evaluation()
    n5_state.n5_boss_advance_pending = False  # as if activate_n5_boss had consumed it
    n5_state.complete_n5_boss_evaluation()  # no new dedicated evaluation: no-op
    assert not n5_state.n5_boss_advance_pending


# --------------------------------------------------------------------------
# Shrink stage transitions
# --------------------------------------------------------------------------

def test_3x3_hit_shrinks_to_2x2_only(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    force_relocation(n5_state, monkeypatch, {2: (3, 3)})
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    set_cluster(n5_state, [(0, 3), (0, 4), (1, 4), (2, 4), (2, 3)], "H1")
    n5_state.evaluate_board()
    assert n5_state.n5_boss_advance_pending
    n5_state.activate_n5_boss()
    assert n5_state.n5_boss_size == 2
    assert n5_state.n5_boss_positions == {(3, 3), (3, 4), (4, 3), (4, 4)}
    assert len(shrunk_events(n5_state)) == 1
    event = shrunk_events(n5_state)[0]
    assert event["previousSize"] == 3 and event["newSize"] == 2


def test_2x2_hit_shrinks_to_1x1_only(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    force_relocation(n5_state, monkeypatch, {2: (3, 3), 1: (5, 5)})
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    set_cluster(n5_state, [(0, 3), (0, 4), (1, 4), (2, 4), (2, 3)], "H1")
    n5_state.evaluate_board()
    n5_state.activate_n5_boss()  # shrink to 2x2 at (3,3)
    assert n5_state.n5_boss_size == 2
    set_cluster(n5_state, [(0, 0), (0, 1), (1, 1), (2, 1), (2, 0)], "H2")  # near (3,3)-(4,4)
    n5_state.board[3][3] = n5_state.board[3][3]  # boss cell stays; add adjacency
    set_cluster(n5_state, [(2, 3)], "H3")  # unrelated filler, avoid accidental match
    n5_state.board[2][3] = n5_state.create_symbol("H3")
    n5_state.board[5][3] = n5_state.create_symbol("H3")
    n5_state.board[5][4] = n5_state.create_symbol("H3")
    n5_state.board[4][5] = n5_state.create_symbol("H3")
    n5_state.evaluate_board()
    assert n5_state.n5_boss_advance_pending
    n5_state.activate_n5_boss()
    assert n5_state.n5_boss_size == 1
    assert n5_state.n5_boss_positions == {(5, 5)}
    assert len(shrunk_events(n5_state)) == 2


def test_1x1_hit_defeats_only(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    assert n5_state.n5_boss_size == 3
    force_relocation(n5_state, monkeypatch, {})
    # Shrink the boss straight down to 1x1 by hand for this focused test.
    n5_state.n5_boss_positions = {(5, 5)}
    n5_state.n5_boss_size = 1
    n5_state.n5_boss_anchor = (5, 5)
    n5_state.board[5][5] = n5_state.create_symbol("WN5")
    n5_state.n5_boss_awaiting_evaluation = True
    set_cluster(n5_state, [(3, 5), (4, 5), (2, 5), (5, 4)], "H1")
    n5_state.evaluate_board()
    assert n5_state.n5_boss_advance_pending
    n5_state.activate_n5_boss()
    assert n5_state.n5_boss_defeated and not n5_state.n5_boss_active
    assert n5_state.n5_boss_positions == set()
    assert len(defeated_events(n5_state)) == 1
    assert not shrunk_events(n5_state)


def test_no_repeated_hit_at_the_same_size(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    force_relocation(n5_state, monkeypatch, {2: (3, 3)})
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    set_cluster(n5_state, [(0, 3), (0, 4), (1, 4), (2, 4), (2, 3)], "H1")
    n5_state.evaluate_board()
    n5_state.activate_n5_boss()
    assert n5_state.n5_boss_size == 2
    # A second call with no new hit must not shrink again.
    n5_state.activate_n5_boss()
    assert n5_state.n5_boss_size == 2
    assert len(shrunk_events(n5_state)) == 1


# --------------------------------------------------------------------------
# Movement
# --------------------------------------------------------------------------

def test_relocation_anchor_always_differs_from_previous(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    for _ in range(20):
        candidate = n5_state.select_n5_relocation_top_left(2, (0, 0))
        assert candidate != (0, 0)


def test_relocation_candidates_exclude_n2_wild_positions(n2_n5_state, monkeypatch):
    ready_board(n2_n5_state)
    pin(n2_n5_state, [(4, 4)])
    force_initial(n2_n5_state, monkeypatch, (0, 0))
    n2_n5_state.level, n2_n5_state.progression = 5, 20
    n2_n5_state.activate_n5_boss()
    candidates = n2_n5_state.n5_boss_candidate_placements(2)
    assert all((4, 4) not in {(c, r) for c in range(p[0], p[0] + 2) for r in range(p[1], p[1] + 2)}
               for p in candidates)


def test_relocation_partial_overlap_with_previous_footprint_allowed(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    force_relocation(n5_state, monkeypatch, {2: (1, 1)})  # overlaps (1,1) of the old 3x3
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    set_cluster(n5_state, [(0, 3), (0, 4), (1, 4), (2, 4), (2, 3)], "H1")
    n5_state.evaluate_board()
    n5_state.activate_n5_boss()
    assert n5_state.n5_boss_anchor == (1, 1)
    assert n5_state.n5_boss_positions == {(1, 1), (1, 2), (2, 1), (2, 2)}
    old_footprint = {(c, r) for c in range(3) for r in range(3)}
    assert n5_state.n5_boss_positions & old_footprint  # genuine partial overlap
    assert n5_state.n5_boss_positions != old_footprint


def test_relocation_is_deterministic_no_reroll_loop(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    calls = []
    def fake_choice(seq):
        calls.append(len(seq))
        return seq[0]
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", fake_choice)
    n5_state.select_n5_relocation_top_left(2, (0, 0))
    assert len(calls) == 1  # exactly one random.choice call, no rejection sampling


# --------------------------------------------------------------------------
# N4 interaction
# --------------------------------------------------------------------------

def test_2x2_may_overlap_n4_zone(n4_n5_state, monkeypatch):
    ready_board(n4_n5_state)
    n4_n5_state.n4_multipliers = {(c, r): 2 for c in range(3) for r in range(3)}
    force_initial(n4_n5_state, monkeypatch, (3, 3))
    force_relocation(n4_n5_state, monkeypatch, {2: (0, 0)})  # overlaps the N4 zone fully
    n4_n5_state.level, n4_n5_state.progression = 5, 20
    n4_n5_state.activate_n5_boss()
    set_cluster(n4_n5_state, [(0, 3), (0, 4), (1, 4), (2, 4), (2, 3)], "H1")
    n4_n5_state.evaluate_board()
    n4_n5_state.activate_n5_boss()
    assert n4_n5_state.n5_boss_positions & set(n4_n5_state.n4_multipliers)


def test_1x1_may_overlap_n4_zone(n4_n5_state, monkeypatch):
    # 2x2 -> 1x1 is the only transition that can ever produce a 1x1 form;
    # relocate straight onto the N4 zone's single position to prove overlap
    # is accepted there (unlike the initial 3x3 -> 2x2 step).
    ready_board(n4_n5_state)
    n4_n5_state.n4_multipliers = {(0, 0): 2}
    force_initial(n4_n5_state, monkeypatch, (3, 3))
    n4_n5_state.level, n4_n5_state.progression = 5, 20
    n4_n5_state.activate_n5_boss()
    n4_n5_state.n5_boss_positions = {(1, 1), (1, 2), (2, 1), (2, 2)}
    n4_n5_state.n5_boss_size = 2
    n4_n5_state.n5_boss_anchor = (1, 1)
    force_relocation(n4_n5_state, monkeypatch, {1: (0, 0)})
    for c, r in n4_n5_state.n5_boss_positions:
        n4_n5_state.board[c][r] = n4_n5_state.create_symbol("WN5")
    n4_n5_state.n5_boss_awaiting_evaluation = True
    n4_n5_state.win_data = {"totalWin": 1.0, "wins": [
        {"symbol": "H1", "positions": [{"reel": 1, "row": 1}, {"reel": 3, "row": 3},
                                        {"reel": 3, "row": 4}, {"reel": 4, "row": 3}, {"reel": 4, "row": 4}]},
    ]}
    n4_n5_state.complete_n5_boss_evaluation()
    assert n4_n5_state.n5_boss_advance_pending
    n4_n5_state.activate_n5_boss()
    assert n4_n5_state.n5_boss_size == 1
    assert n4_n5_state.n5_boss_positions == {(0, 0)}
    assert (0, 0) in n4_n5_state.n4_multipliers  # the overlapped N4 position is untouched


def test_n4_multiplier_contributes_to_boss_assisted_win(n4_n5_state, monkeypatch):
    ready_board(n4_n5_state)
    n4_n5_state.n4_multipliers = {(0, 0): 3}
    force_initial(n4_n5_state, monkeypatch, (0, 0))
    n4_n5_state.level, n4_n5_state.progression = 5, 20
    n4_n5_state.activate_n5_boss()
    set_cluster(n4_n5_state, [(0, 3), (0, 4), (1, 4), (2, 4), (2, 3)], "H1")
    n4_n5_state.evaluate_board()
    h1 = [w for w in n4_n5_state.win_data["wins"] if w["symbol"] == "H1"][0]
    assert h1["meta"]["clusterMult"] == 3


def test_n4_multiplier_upgrades_after_boss_assisted_payout(n4_n5_state, monkeypatch):
    ready_board(n4_n5_state)
    n4_n5_state.n4_multipliers = {(0, 0): 2}
    force_initial(n4_n5_state, monkeypatch, (0, 0))
    n4_n5_state.level, n4_n5_state.progression = 5, 20
    n4_n5_state.activate_n5_boss()
    set_cluster(n4_n5_state, [(0, 3), (0, 4), (1, 4), (2, 4), (2, 3)], "H1")
    n4_n5_state.evaluate_board()
    h1 = [w for w in n4_n5_state.win_data["wins"] if w["symbol"] == "H1"][0]
    assert h1["meta"]["clusterMult"] == 2  # pre-upgrade value used for this payout
    # evaluate_board() already called upgrade_n4_multipliers() internally.
    assert n4_n5_state.n4_multipliers[(0, 0)] == 3


def test_n4_multiplier_stays_at_position_after_boss_moves_away(n4_n5_state, monkeypatch):
    ready_board(n4_n5_state)
    n4_n5_state.n4_multipliers = {(0, 0): 4}
    force_initial(n4_n5_state, monkeypatch, (0, 0))
    force_relocation(n4_n5_state, monkeypatch, {2: (3, 3)})
    n4_n5_state.level, n4_n5_state.progression = 5, 20
    n4_n5_state.activate_n5_boss()
    set_cluster(n4_n5_state, [(0, 3), (0, 4), (1, 4), (2, 4), (2, 3)], "H1")
    n4_n5_state.evaluate_board()
    n4_n5_state.activate_n5_boss()
    assert (0, 0) not in n4_n5_state.n5_boss_positions
    # The position's own participation in the payout upgraded it normally
    # (4 -> 5, capped); the entry is not lost or reset by the boss leaving.
    assert n4_n5_state.n4_multipliers[(0, 0)] == 5


def test_n4_multiplier_stays_after_boss_defeat(n4_n5_state, monkeypatch):
    ready_board(n4_n5_state)
    n4_n5_state.n4_multipliers = {(5, 5): 3}
    force_initial(n4_n5_state, monkeypatch, (0, 0))
    n4_n5_state.level, n4_n5_state.progression = 5, 20
    n4_n5_state.activate_n5_boss()
    n4_n5_state.n5_boss_positions = {(5, 5)}
    n4_n5_state.n5_boss_size = 1
    n4_n5_state.n5_boss_anchor = (5, 5)
    n4_n5_state.board[5][5] = n4_n5_state.create_symbol("WN5")
    n4_n5_state.n5_boss_awaiting_evaluation = True
    set_cluster(n4_n5_state, [(3, 5), (4, 5), (2, 5), (5, 4)], "H1")
    n4_n5_state.evaluate_board()
    n4_n5_state.activate_n5_boss()
    assert n4_n5_state.n5_boss_defeated
    # The position's own participation upgraded it normally (3 -> 4); the
    # entry survives the boss's own removal from the board.
    assert n4_n5_state.n4_multipliers[(5, 5)] == 4


# --------------------------------------------------------------------------
# N2/N4 Wild interaction
# --------------------------------------------------------------------------

def test_n2_sticky_state_survives_boss_lifecycle(n2_n5_state, monkeypatch):
    ready_board(n2_n5_state)
    pin(n2_n5_state, [(5, 5)])
    force_initial(n2_n5_state, monkeypatch, (0, 0))
    force_relocation(n2_n5_state, monkeypatch, {2: (3, 3), 1: (2, 2)})
    n2_n5_state.level, n2_n5_state.progression = 5, 20
    n2_n5_state.activate_n5_boss()
    set_cluster(n2_n5_state, [(0, 3), (0, 4), (1, 4), (2, 4), (2, 3)], "H1")
    n2_n5_state.evaluate_board()
    n2_n5_state.activate_n5_boss()
    assert n2_n5_state.board[5][5].name == "W"
    assert n2_n5_state.board[5][5].check_attribute("sticky")
    assert (5, 5) in n2_n5_state.sticky_positions


def test_n4_wn4_lifecycle_unaffected_by_boss(n4_n5_state, monkeypatch):
    ready_board(n4_n5_state)
    n4_n5_state.n4_multipliers = {(5, 0): 2}
    n4_n5_state.board[5][0] = n4_n5_state.create_symbol("WN4")
    force_initial(n4_n5_state, monkeypatch, (0, 0))
    n4_n5_state.level, n4_n5_state.progression = 5, 20
    n4_n5_state.activate_n5_boss()
    n4_n5_state.evaluate_board()
    assert n4_n5_state.board[5][0].name == "WN4"


def test_wn5_distinguishable_from_w_and_wn4(n2_n4_n5_state, monkeypatch):
    ready_board(n2_n4_n5_state)
    pin(n2_n4_n5_state, [(5, 5)])
    n2_n4_n5_state.n4_multipliers = {(5, 0): 2}
    n2_n4_n5_state.board[5][0] = n2_n4_n5_state.create_symbol("WN4")
    force_initial(n2_n4_n5_state, monkeypatch, (0, 0))
    n2_n4_n5_state.level, n2_n4_n5_state.progression = 5, 20
    n2_n4_n5_state.activate_n5_boss()
    from kitchen_vs_fly.game_events import board_snapshot
    snap = board_snapshot(n2_n4_n5_state)
    assert snap[5][5] == {"name": "W", "wild": True, "sticky": True}
    assert snap[5][0] == {"name": "WN4", "wild": True}
    assert snap[0][0] == {"name": "WN5", "wild": True}


# --------------------------------------------------------------------------
# Gravity / cascades
# --------------------------------------------------------------------------

def test_boss_cells_do_not_fall_during_an_unrelated_win(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (2, 2))  # column 2 occupied at rows 2-4
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    before = dict(n5_state.n5_boss_positions and {p: n5_state.board[p[0]][p[1]] for p in n5_state.n5_boss_positions})
    set_cluster(n5_state, [(2, 5), (2, 0), (2, 1), (0, 5), (1, 5)], "H1")
    for c, r in [(2, 5), (2, 0), (2, 1), (0, 5), (1, 5)]:
        n5_state.board[c][r].explode = True
    n5_state.tumble_board()
    for pos, symbol in before.items():
        assert n5_state.board[pos[0]][pos[1]] is symbol
        assert n5_state.board[pos[0]][pos[1]].name == "WN5"


def test_footprint_remains_coherent_after_cascades(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (2, 2))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    for c, r in [(0, 0), (0, 1), (1, 0)]:
        n5_state.board[c][r].explode = True
    n5_state.tumble_board()
    on_board = {(c, r) for c in range(6) for r in range(6) if n5_state.board[c][r].name == "WN5"}
    assert on_board == n5_state.n5_boss_positions


def test_no_duplicate_exploding_positions_from_boss_bridged_wins(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (2, 2))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    set_cluster(n5_state, [(0, 2), (0, 3), (1, 2)], "H1")
    set_cluster(n5_state, [(5, 2), (5, 3), (4, 2)], "H2")
    n5_state.evaluate_board()
    from kitchen_vs_fly.game_events import cascade_event
    n5_state.tumble_board()
    n5_state.cascade_count += 1
    cascade_event(n5_state)
    event = n5_state.book.events[-1]
    positions = [(p["reel"], p["row"]) for p in event["explodingSymbols"]]
    assert len(positions) == len(set(positions))


# --------------------------------------------------------------------------
# Defeat
# --------------------------------------------------------------------------

def test_defeat_removes_boss_and_emits_exactly_one_event(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    n5_state.n5_boss_positions = {(5, 5)}
    n5_state.n5_boss_size = 1
    n5_state.n5_boss_anchor = (5, 5)
    n5_state.board[5][5] = n5_state.create_symbol("WN5")
    n5_state.n5_boss_awaiting_evaluation = True
    set_cluster(n5_state, [(3, 5), (4, 5), (2, 5), (5, 4)], "H1")
    n5_state.evaluate_board()
    n5_state.activate_n5_boss()
    assert len(defeated_events(n5_state)) == 1
    on_board = {(c, r) for c in range(6) for r in range(6) if n5_state.board[c][r].name == "WN5"}
    assert on_board == set()


def test_boss_never_returns_after_defeat(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    n5_state.n5_boss_positions = {(5, 5)}
    n5_state.n5_boss_size = 1
    n5_state.n5_boss_anchor = (5, 5)
    n5_state.board[5][5] = n5_state.create_symbol("WN5")
    n5_state.n5_boss_awaiting_evaluation = True
    set_cluster(n5_state, [(3, 5), (4, 5), (2, 5), (5, 4)], "H1")
    n5_state.evaluate_board()
    n5_state.activate_n5_boss()
    assert n5_state.n5_boss_defeated
    n5_state.activate_n5_boss()  # must stay a no-op forever after
    assert len(defeated_events(n5_state)) == 1


def test_defeat_grants_no_automatic_extra_payout(n5_state, monkeypatch):
    ready_board(n5_state)
    force_initial(n5_state, monkeypatch, (0, 0))
    n5_state.level, n5_state.progression = 5, 20
    n5_state.activate_n5_boss()
    n5_state.n5_boss_positions = {(5, 5)}
    n5_state.n5_boss_size = 1
    n5_state.n5_boss_anchor = (5, 5)
    n5_state.board[5][5] = n5_state.create_symbol("WN5")
    n5_state.n5_boss_awaiting_evaluation = True
    set_cluster(n5_state, [(3, 5), (4, 5), (2, 5), (5, 4)], "H1")
    n5_state.evaluate_board()
    win_before_defeat = n5_state.win_data["totalWin"]
    n5_state.activate_n5_boss()
    assert n5_state.win_data["totalWin"] == win_before_defeat  # no bonus payout attached


# --------------------------------------------------------------------------
# Full-round replay and regression
# --------------------------------------------------------------------------

def test_full_round_with_n5_replays_deterministically(n2_n4_n5_state, monkeypatch):
    n2_n4_n5_state.config.n3_conversion_enabled = True
    n2_n4_n5_state.run_spin(0, 999)
    first = deepcopy(n2_n4_n5_state.book.to_json())
    validate_book(first, n2_n4_n5_state.config)
    n2_n4_n5_state.run_spin(0, 999)
    second = n2_n4_n5_state.book.to_json()
    assert first == second
    validate_book(second, n2_n4_n5_state.config)


def test_production_rounds_with_n5_replay_cleanly():
    from kitchen_vs_fly.game_config import GameConfig
    from kitchen_vs_fly.gamestate import GameState
    config = GameConfig()
    game = GameState(config)
    game.betmode = "base"
    game.criteria = "natural"
    saw_boss = False
    saw_defeat = False
    for sim in range(400):
        game.run_spin(sim, simulation_seed=sim)
        book = game.book.to_json()
        types = {e["type"] for e in book["events"]}
        saw_boss = saw_boss or "bossTriggered" in types
        saw_defeat = saw_defeat or "bossDefeated" in types
        validate_book(book, config)
    assert saw_boss, "sample did not exercise N5 activation; widen it"
    assert saw_defeat, "sample did not exercise a boss defeat; widen it"


def test_n1_n2_n3_n4_still_pass_with_n5_enabled(n2_n4_n5_state, monkeypatch):
    n2_n4_n5_state.config.n3_conversion_enabled = True
    for sim in range(200):
        n2_n4_n5_state.run_spin(sim, simulation_seed=sim)
        book = n2_n4_n5_state.book.to_json()
        validate_book(book, n2_n4_n5_state.config)


# --------------------------------------------------------------------------
# Replay corruption
# --------------------------------------------------------------------------

# Found by search among production-config seeds (n2 + n5 active, n3/n4/sword
# off) for one that naturally reaches a full 3x3 -> 2x2 -> 1x1 -> defeated
# lifecycle under the real reel distribution, rather than hand-engineering a
# board: simpler to keep correct than predicting gravity/refill by hand.
FULL_LIFECYCLE_SEED = 598


def full_n5_round(state, seed=FULL_LIFECYCLE_SEED):
    state.run_spin(seed, seed)
    book = deepcopy(state.book.to_json())
    assert [e["type"] for e in book["events"]].count("bossDefeated") == 1
    assert [e["type"] for e in book["events"]].count("bossShrunk") == 2
    return book


def reindex(sequence):
    for i, event in enumerate(sequence):
        event["index"] = i


def test_replay_rejects_boss_activation_before_threshold(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    i = next(i for i, e in enumerate(sequence) if e["type"] == "bossTriggered")
    sequence[i]["progression"] -= 100
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)


def test_replay_rejects_duplicate_boss_activation(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    i = next(i for i, e in enumerate(sequence) if e["type"] == "bossTriggered")
    sequence.insert(i, deepcopy(sequence[i]))
    reindex(sequence)
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)


def test_replay_rejects_invalid_initial_footprint_size(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    trigger = next(e for e in sequence if e["type"] == "bossTriggered")
    trigger["footprint"] = trigger["footprint"][:8]  # only 8 cells
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)


def test_replay_rejects_initial_footprint_outside_grid(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    trigger = next(e for e in sequence if e["type"] == "bossTriggered")
    trigger["anchor"] = {"reel": 5, "row": 5}
    trigger["footprint"] = [{"reel": c, "row": r} for c in range(5, 8) for r in range(5, 8)]
    with pytest.raises((AssertionError, KeyError, IndexError)):
        validate_book(book, n2_n5_state.config)


def test_replay_rejects_falsified_initial_anchor(n4_n5_state, monkeypatch):
    def draw():
        ready_board(n4_n5_state)
        set_cluster(n4_n5_state, [(c, 0) for c in range(5)])
    monkeypatch.setattr(n4_n5_state, "draw_board", draw)
    force_zone(n4_n5_state, monkeypatch, (1, 1))  # every 3x3 boss candidate overlaps this zone
    force_sample(monkeypatch)
    force_initial(n4_n5_state, monkeypatch, (0, 0))  # (0,0) has the minimal 1-cell overlap
    n4_n5_state.config.progression_rules = ProgressionRules(thresholds=(1, 2, 3, 4))
    n4_n5_state.run_spin(0, 0)
    book = deepcopy(n4_n5_state.book.to_json())
    sequence = book["events"]
    trigger = next(e for e in sequence if e["type"] == "bossTriggered")
    # (2,3) has a much larger overlap with the (1,1) N4 zone than the actual
    # minimal-overlap placement (0,0); the geometry check must reject it.
    trigger["anchor"] = {"reel": 2, "row": 3}
    trigger["footprint"] = [{"reel": c, "row": r} for c in range(2, 5) for r in range(3, 6)]
    with pytest.raises(AssertionError):
        validate_book(book, n4_n5_state.config)


def test_replay_rejects_boss_cells_inconsistent_with_size(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    trigger = next(e for e in sequence if e["type"] == "bossTriggered")
    trigger["footprint"].append({"reel": 5, "row": 5})  # 10 cells for a "size": 3 event
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)


def test_replay_rejects_invalid_size_transition(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    shrink = next(e for e in sequence if e["type"] == "bossShrunk" and e["previousSize"] == 3)
    shrink["newSize"] = 1  # skips 2x2
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)


def test_replay_rejects_boss_hit_without_participating_in_a_paid_win(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    shrink_idx = next(i for i, e in enumerate(sequence) if e["type"] == "bossShrunk")
    # Remove the winInfo that actually produced the hit, keeping the shrink.
    win_idx = next(i for i in range(shrink_idx - 1, -1, -1) if sequence[i]["type"] == "winInfo")
    del sequence[win_idx]
    reindex(sequence)
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)


def test_replay_rejects_unchanged_anchor_after_shrink(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    shrink = next(e for e in sequence if e["type"] == "bossShrunk")
    shrink["newAnchor"] = shrink["previousAnchor"]
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)


def test_replay_rejects_new_footprint_outside_grid(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    shrink = next(e for e in sequence if e["type"] == "bossShrunk")
    shrink["newAnchor"] = {"reel": 5, "row": 5}
    shrink["newFootprint"] = [{"reel": 5, "row": 5}, {"reel": 6, "row": 5},
                               {"reel": 5, "row": 6}, {"reel": 6, "row": 6}]
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)


def test_replay_rejects_n2_wild_overwritten_by_relocation(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    # Seed 598's own N2 sticky positions (see FULL_LIFECYCLE_SEED docstring).
    sticky = {(1, 2), (3, 1)}
    shrink = next(e for e in sequence if e["type"] == "bossShrunk" and e["newSize"] == 2)
    # Relocate the 2x2 boss directly onto (1,2), one of the round's own N2
    # persistent Wild positions.
    shrink["newAnchor"] = {"reel": 1, "row": 1}
    shrink["newFootprint"] = [{"reel": c, "row": r} for c in (1, 2) for r in (1, 2)]
    assert {(p["reel"], p["row"]) for p in shrink["newFootprint"]} & sticky
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)


def test_relocation_raises_when_no_n2_safe_candidate_exists(n2_n5_state, monkeypatch):
    """Direct unit-level check of the method's own defensive guard (distinct
    from the replay-corruption test above): if every candidate of a given
    size overlaps an N2 Wild, select_n5_relocation_top_left must fail loudly
    rather than silently overwrite one."""
    ready_board(n2_n5_state)
    force_initial(n2_n5_state, monkeypatch, (2, 2))
    n2_n5_state.level, n2_n5_state.progression = 5, 20
    n2_n5_state.activate_n5_boss()
    # Pin every one of the 36 positions as sticky: no 1x1 candidate can exist.
    n2_n5_state.sticky_positions = {(c, r) for c in range(6) for r in range(6)}
    with pytest.raises(RuntimeError):
        n2_n5_state.select_n5_relocation_top_left(1, (2, 2))


def test_replay_rejects_boss_only_win_in_book(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    win = next(e for e in sequence if e["type"] == "winInfo")
    forged = deepcopy(win["wins"][0])
    forged["symbol"], forged["win"] = "WN5", 999
    win["wins"].append(forged)
    win["totalWin"] += 999
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)


def test_replay_rejects_duplicate_defeat_event(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    defeats = [e for e in sequence if e["type"] == "bossDefeated"]
    if not defeats:
        pytest.skip("this deterministic scenario did not reach defeat")
    i = next(i for i, e in enumerate(sequence) if e["type"] == "bossDefeated")
    sequence.insert(i + 1, deepcopy(sequence[i]))
    reindex(sequence)
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)


def test_replay_rejects_falsified_board_snapshot(n2_n5_state):
    book = full_n5_round(n2_n5_state)
    sequence = book["events"]
    trigger = next(e for e in sequence if e["type"] == "bossTriggered")
    trigger["board"][0][0] = {"name": "H4"}
    with pytest.raises(AssertionError):
        validate_book(book, n2_n5_state.config)
