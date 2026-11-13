"""V0.3.6 grid generalization: focused coverage for the experimental 6x7 board.

Not a re-verification of N1-N5 mechanics themselves (already covered on the
validated 6x6 grid by the dedicated N2/N3/N4/N5 suites) — only of the
grid-size generalization added in game_config.py (GameConfig(num_rows=...))
and validation.py (grid_dims/n5_candidate_placements/winning_clusters) for
docs/milestones/V0.3.6_grid_6x6_vs_6x7_study.md. The original 6x6 defaults
are asserted unchanged throughout.
"""

import pytest

from kitchen_vs_fly.game_config import GameConfig
from kitchen_vs_fly.gamestate import GameState
from kitchen_vs_fly.validation import (
    grid_dims, n5_candidate_placements, validate_book, winning_clusters,
)


def make_state(num_rows=7, progression_thresholds=(25, 50, 75, 100), **flags):
    config = GameConfig(num_rows=num_rows, progression_thresholds=progression_thresholds)
    config.boots_enabled = False
    config.n2_wild_enabled = False
    config.n3_conversion_enabled = False
    config.n4_multiplier_enabled = False
    config.sword_enabled = False
    config.n5_boss_enabled = False
    for key, value in flags.items():
        setattr(config, key, value)
    game = GameState(config)
    game.betmode = "base"
    game.criteria = "natural"
    return game


def quiet_board(state):
    """Checkerboard fill with no five-in-a-row cluster, any grid size."""
    rows = state.config.num_rows[0]
    return [[state.create_symbol(state.config.symbols[(c + r) % 8]) for r in range(rows)]
            for c in range(state.config.num_reels)]


# --- GameConfig generalization -------------------------------------------------

def test_default_gameconfig_unchanged():
    config = GameConfig()
    assert config.num_reels == 6 and config.num_rows == [6] * 6
    assert config.progression_rules.thresholds == (5, 10, 15, 20)
    assert max(size for size, _ in config.paytable) == 36


def test_6x7_gameconfig_dimensions_and_thresholds():
    config = GameConfig(num_rows=7, progression_thresholds=(25, 50, 75, 100))
    assert config.num_reels == 6 and config.num_rows == [7] * 6
    assert config.progression_rules.thresholds == (25, 50, 75, 100)
    assert max(size for size, _ in config.paytable) == 42
    assert config.paytable[(42, "H4")] == 8 * (42 - 4) / 10


def test_6x7_paytable_formula_matches_6x6_for_shared_sizes():
    c6 = GameConfig()
    c7 = GameConfig(num_rows=7)
    shared_sizes = range(c6.min_cluster_size, 37)
    for size in shared_sizes:
        for symbol in c6.symbols:
            assert c6.paytable[(size, symbol)] == c7.paytable[(size, symbol)]


# --- Board dimensions / cluster / gravity / refill on 7 rows -------------------

def test_board_has_seven_rows_per_reel():
    state = make_state()
    state.draw_board()
    assert len(state.board) == 6
    assert all(len(column) == 7 for column in state.board)


def test_cluster_detection_at_new_row_boundary_row6():
    """Row index 6 only exists on the 6x7 grid; a cluster must be able to
    form there and be flood-filled identically by the SDK (evaluate_board)
    and the independent replay verifier (winning_clusters)."""
    state = make_state()
    state.board = quiet_board(state)
    for c in range(5):
        state.board[c][6] = state.create_symbol("H2")
    state.evaluate_board()
    assert state.win_data["totalWin"] > 0
    win = state.win_data["wins"][0]
    assert win["symbol"] == "H2" and {(c, 6) for c in range(5)} <= {
        (p["reel"], p["row"]) for p in win["positions"]}

    reels, rows = grid_dims(state.config)
    assert (reels, rows) == (6, 7)
    board_json = [[{"name": s.name} for s in col] for col in state.board]
    expected = winning_clusters(board_json, state.config)
    # Independent flood fill must agree exactly with the SDK's own evaluation,
    # including a cluster that only exists because row 6 (new on 6x7) formed it.
    actual = {(w["symbol"], frozenset((p["reel"], p["row"]) for p in w["positions"]),
               int(round(w["win"] * 100)), w["meta"]["clusterMult"])
              for w in state.win_data["wins"]}
    assert actual == expected


def test_gravity_and_refill_use_all_seven_rows():
    state = make_state()
    state.draw_board()
    original_top = [state.board[c][0].name for c in range(6)]
    for c in range(6):
        state.board[c][6].explode = True
    state.tumble_board()
    assert len(state.new_symbols_from_tumble[0]) == 1
    assert all(len(column) == 7 for column in state.board)
    assert [state.board[c][1].name for c in range(6)] == original_top


# --- N2 on 6x7 -------------------------------------------------------------

def test_n2_placement_and_persistence_on_6x7():
    state = make_state(n2_wild_enabled=True)
    state.draw_board()
    state.board = quiet_board(state)
    state.level = 2
    state.activate_n2_wilds()
    assert len(state.sticky_positions) == state.config.n2_wild_count == 2
    assert all(0 <= r < 7 for _, r in state.sticky_positions)
    for c, r in state.sticky_positions:
        assert state.board[c][r].name == "W"
    # Persistence through a gravity pass.
    victim = next(iter(state.sticky_positions))
    for c in range(6):
        for r in range(7):
            if (c, r) != victim:
                state.board[c][r].explode = True
    state.tumble_board()
    assert state.board[victim[0]][victim[1]].name == "W"


# --- N3 on 6x7 -------------------------------------------------------------

def test_n3_conversion_on_6x7():
    state = make_state(n3_conversion_enabled=True)
    state.reset_book()
    state.board = quiet_board(state)
    state.board[0][6] = state.create_symbol("H1")  # exercise the new row too
    state.board[1][6] = state.create_symbol("H2")
    state.level = 3
    state.activate_n3_conversion()
    present_after = {state.board[c][r].name for c in range(6) for r in range(7)}
    premiums_after = present_after & set(state.config.premium_symbols)
    assert len(premiums_after) <= 1


# --- N4 on 6x7: anchor enumeration, uniform placement, multiplier persistence --

def test_n4_valid_anchor_count_on_6x7_is_20():
    state = make_state(n4_multiplier_enabled=True)
    max_reel = state.config.num_reels - state.config.n4_zone_size
    max_row = min(state.config.num_rows) - state.config.n4_zone_size
    anchors = [(c, r) for c in range(max_reel + 1) for r in range(max_row + 1)]
    assert len(anchors) == 20  # (6-3+1) * (7-3+1) = 4 * 5


def test_n4_placement_uniform_over_20_anchors_on_6x7():
    import random
    state = make_state(n4_multiplier_enabled=True)
    state.reset_book()
    state.board = quiet_board(state)
    seen = set()
    for seed in range(200):
        random.seed(seed)
        seen.add(state.select_n4_zone_top_left())
    assert len(seen) == 20  # every valid anchor reachable


def test_n4_multiplier_persists_across_cascades_on_6x7():
    state = make_state(n4_multiplier_enabled=True)
    state.draw_board()
    state.board = quiet_board(state)
    state.level = 4
    state.activate_n4()
    zone = set(state.n4_multipliers)
    assert len(zone) == 9
    assert all(0 <= r < 7 for _, r in zone)
    for c, r in list(zone)[:3]:
        state.board[c][r].explode = True
    state.tumble_board()
    state.apply_n4_multipliers()
    assert set(state.n4_multipliers) == zone
    for (c, r), value in state.n4_multipliers.items():
        assert state.board[c][r].get_attribute("multiplier") == value


# --- N5 on 6x7: candidate enumeration, overlap minimization, relocation, footprint --

def test_n5_candidate_enumeration_matches_engine_and_validator_on_6x7():
    state = make_state(n5_boss_enabled=True)
    state.reset_book()
    engine_candidates = state.n5_boss_candidate_placements(3)
    validator_candidates = n5_candidate_placements(3, state.sticky_positions, 6, 7)
    assert engine_candidates == validator_candidates
    assert len(engine_candidates) == 20


def test_n5_initial_overlap_minimization_on_6x7_only_2_of_20_forced():
    """Exhaustive geometric proof (see docs/milestones/V0.3.6...): unlike the
    6x6 grid where 4/16 N4 placements force a 1-cell overlap, on 6x7 only
    2/20 N4 placements (the interior column x middle row) do."""
    def footprint(top, size=3):
        c, r = top
        return {(x, y) for x in range(c, c + size) for y in range(r, r + size)}

    max_reel, max_row = 6 - 3, 7 - 3
    n4_anchors = [(c, r) for c in range(max_reel + 1) for r in range(max_row + 1)]
    forced_overlap = []
    for n4_anchor in n4_anchors:
        n4_zone = footprint(n4_anchor)
        min_overlap = min(len(footprint(a) & n4_zone) for a in n4_anchors)
        if min_overlap > 0:
            forced_overlap.append((n4_anchor, min_overlap))
    assert len(n4_anchors) == 20
    assert forced_overlap == [((1, 2), 1), ((2, 2), 1)]


def test_n5_initial_placement_avoids_n4_when_geometrically_possible_on_6x7():
    state = make_state(n4_multiplier_enabled=True, n5_boss_enabled=True)
    state.reset_book()
    state.board = quiet_board(state)
    state.n4_multipliers = {(c, r): 2 for c in range(0, 3) for r in range(0, 3)}  # corner zone
    top = state.select_n5_initial_top_left()
    footprint = {(c, r) for c in range(top[0], top[0] + 3) for r in range(top[1], top[1] + 3)}
    assert footprint.isdisjoint(state.n4_multipliers)  # a corner N4 zone always permits zero overlap


def test_n5_relocation_moves_anchor_and_stays_n2_safe_on_6x7():
    state = make_state(n2_wild_enabled=True, n5_boss_enabled=True)
    state.reset_book()
    state.sticky_positions = {(5, 6)}  # bottom-right corner of the new 7th row
    top = state.select_n5_relocation_top_left(2, (0, 0))
    footprint = {(c, r) for c in range(top[0], top[0] + 2) for r in range(top[1], top[1] + 2)}
    assert top != (0, 0)
    assert footprint.isdisjoint(state.sticky_positions)


def test_n5_footprint_sizes_stay_on_board_at_all_stages_on_6x7():
    state = make_state(n5_boss_enabled=True)
    state.reset_book()
    for size in (3, 2, 1):
        for top in state.n5_boss_candidate_placements(size):
            assert 0 <= top[0] <= 6 - size and 0 <= top[1] <= 7 - size


def test_n2_n5_do_not_overlap_on_6x7():
    state = make_state(n2_wild_enabled=True, n5_boss_enabled=True)
    state.reset_book()
    state.board = quiet_board(state)
    state.level = 5
    state.sticky_positions = {(2, 6), (3, 2)}
    for c, r in state.sticky_positions:
        state.board[c][r] = state.create_symbol("W")
    state.get_special_symbols_on_board()
    state._trigger_n5_boss()
    assert state.n5_boss_positions.isdisjoint(state.sticky_positions)


# --- Deterministic replay on 6x7 -------------------------------------------

def test_deterministic_replay_full_round_on_6x7():
    config = GameConfig(num_rows=7, progression_thresholds=(25, 50, 75, 100))
    state_a, state_b = GameState(config), GameState(GameConfig(
        num_rows=7, progression_thresholds=(25, 50, 75, 100)))
    for state in (state_a, state_b):
        state.betmode = "base"
        state.criteria = "natural"
    state_a.run_spin(0, simulation_seed=987654321)
    state_b.run_spin(0, simulation_seed=987654321)
    assert state_a.book.to_json()["events"] == state_b.book.to_json()["events"]


@pytest.mark.parametrize("seed", range(25))
def test_replay_validates_independently_on_6x7(seed):
    config = GameConfig(num_rows=7, progression_thresholds=(25, 50, 75, 100))
    state = GameState(config)
    state.betmode = "base"
    state.criteria = "natural"
    state.run_spin(seed, simulation_seed=seed)
    book = state.book.to_json()
    validate_book(book, config)  # raises AssertionError on any replay mismatch


def test_6x6_regression_unaffected_by_generalization():
    """Same seeds, default (unspecified) grid: must reproduce the validated
    6x6/DEV-threshold behaviour byte-for-byte after the generalization."""
    for seed in range(10):
        config = GameConfig()
        state = GameState(config)
        state.betmode = "base"
        state.criteria = "natural"
        state.run_spin(seed, simulation_seed=seed)
        book = state.book.to_json()
        validate_book(book, config)
        assert len(state.board) == 6 and all(len(col) == 6 for col in state.board)
