"""V0.3.10 min_cluster_size=6 follow-up: `GameConfig(production=True,
min_cluster_size=6)` installs the re-derived pairing for that regime --
16/32/48/64 thresholds and CANDIDATE_A_MIN6_PAYTABLE_STEPS -- found because
raising the floor alone (no other change) collapses RTP from 96.72% to
20.37% and crushes N2/N5 access rates (see docs/milestones/
V0.3.10_n5_rtp_contribution_and_reward_curve_review.md §7).
`min_cluster_size` defaults to 5 regardless of `production`, so every
pre-V0.3.10 caller (including bare `GameConfig(production=True)`) stays
completely unaffected -- several tests below assert that explicitly.
"""

from kitchen_vs_fly.game_config import (
    CANDIDATE_A_MIN6_PAYTABLE_STEPS,
    CANDIDATE_A_PAYTABLE_STEPS,
    GameConfig,
)
from kitchen_vs_fly.gamestate import GameState
from kitchen_vs_fly.validation import validate_book


def test_min6_thresholds_and_paytable_selection():
    config = GameConfig(production=True, min_cluster_size=6)
    assert config.min_cluster_size == 6
    assert config.progression_rules.thresholds == (16, 32, 48, 64)
    assert (5, "L1") not in config.paytable  # floor raised: size 5 no longer wins
    assert config.paytable[(6, "L1")] == 0.10
    assert config.paytable[(6, "H4")] == 1.80
    assert config.paytable[(30, "H4")] == 662.00


def test_min6_paytable_is_monotonic_and_rgs_compatible():
    for symbol, steps in CANDIDATE_A_MIN6_PAYTABLE_STEPS.items():
        breakpoints = sorted(steps)
        values = [steps[bp] for bp in breakpoints]
        assert values == sorted(values), f"{symbol} steps not non-decreasing"
        for value in values:
            assert value > 0
            assert round(value * 100) % 10 == 0, f"{value} is not a multiple of 0.10"


def test_min6_does_not_affect_default_production():
    config = GameConfig(production=True)
    assert config.min_cluster_size == 5
    assert config.progression_rules.thresholds == (20, 40, 60, 80)
    assert config.paytable[(5, "H4")] == 0.70  # unchanged Candidate M value
    assert config.paytable is not CANDIDATE_A_MIN6_PAYTABLE_STEPS
    assert config.paytable[(6, "H4")] == CANDIDATE_A_PAYTABLE_STEPS["H4"][6]


def test_min6_explicit_threshold_override_still_wins():
    config = GameConfig(production=True, min_cluster_size=6, progression_thresholds=(1, 2, 3, 4))
    assert config.progression_rules.thresholds == (1, 2, 3, 4)
    assert config.min_cluster_size == 6  # override applies only to thresholds


def test_min6_access_rates_within_broad_tolerance():
    """Coarse anti-regression guard, not a precision RTP/frequency test (see
    docs/milestones/V0.3.10_n5_rtp_contribution_and_reward_curve_review.md
    §7 for the calibrated figures: N2 ~1/14.0, N5 ~1/126.8 over 200,000
    books, RTP 96.62%)."""
    config = GameConfig(production=True, min_cluster_size=6)
    game = GameState(config)
    game.betmode = "base"
    game.criteria = "natural"
    n_books = 8000
    level_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for sim in range(n_books):
        game.run_spin(sim, simulation_seed=sim)
        level_counts[game.level] += 1
    n2 = sum(level_counts[level] for level in (2, 3, 4, 5))
    n5 = level_counts[5]
    assert 0.03 * n_books <= n2 <= 0.15 * n_books, f"N2 access rate out of broad range: {n2}/{n_books}"
    assert 0.002 * n_books <= n5 <= 0.02 * n_books, f"N5 access rate out of broad range: {n5}/{n_books}"


def test_min6_replays_cleanly():
    config = GameConfig(production=True, min_cluster_size=6)
    game = GameState(config)
    game.betmode = "base"
    game.criteria = "natural"
    for sim in range(300):
        game.run_spin(sim, simulation_seed=sim + 950000)
        validate_book(game.book.to_json(), config)
