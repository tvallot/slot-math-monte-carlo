"""V0.3.9 production configuration: `GameConfig(production=True)` installs
the calibrated "Candidate A" (see docs/milestones/
V0.3.9_candidate_a_calibration.md) — 20/40/60/80 thresholds, individually
skewed 70/30 symbol weights, N2's 2 fixed + 3 temporary Wilds, and a real
step-function paytable capped at cluster size 30. `production` defaults to
False, so every pre-V0.3.9 caller (including `GameConfig()` bare) must stay
completely unaffected — several tests below assert that explicitly.
"""

from kitchen_vs_fly.game_config import (
    CANDIDATE_A_PAYTABLE_STEPS,
    CANDIDATE_A_SYMBOL_WEIGHTS,
    GameConfig,
)
from kitchen_vs_fly.gamestate import GameState
from kitchen_vs_fly.validation import validate_book


def test_production_thresholds_and_weights():
    config = GameConfig(production=True)
    assert config.progression_rules.thresholds == (20, 40, 60, 80)
    strips_symbols = {symbol for column in config.reels["DEV"] for symbol in column}
    assert strips_symbols == set(CANDIDATE_A_SYMBOL_WEIGHTS)
    # Onion/garlic must be weighted well above butter/herbs among the lows,
    # per explicit product direction (not a flat 17.5% each).
    assert CANDIDATE_A_SYMBOL_WEIGHTS["L1"] == CANDIDATE_A_SYMBOL_WEIGHTS["L2"] > \
        CANDIDATE_A_SYMBOL_WEIGHTS["L3"] == CANDIDATE_A_SYMBOL_WEIGHTS["L4"]
    low_total = sum(CANDIDATE_A_SYMBOL_WEIGHTS[s] for s in ("L1", "L2", "L3", "L4"))
    high_total = sum(CANDIDATE_A_SYMBOL_WEIGHTS[s] for s in ("H1", "H2", "H3", "H4"))
    assert low_total == 70 and high_total == 30


def test_production_n2_mixed_wilds():
    config = GameConfig(production=True)
    assert config.n2_wild_enabled is True
    assert config.n2_wild_count == 2
    assert config.n2_temp_wild_count == 3


def test_production_paytable_matches_calibrated_steps_and_caps_at_30():
    config = GameConfig(production=True)
    assert config.paytable[(5, "L1")] == 0.10
    assert config.paytable[(5, "H4")] == 0.70  # V0.3.10 reward-curve reshape, see game_config.py
    assert config.paytable[(20, "H4")] == 80.00
    assert config.paytable[(25, "H4")] == 120.00
    assert config.paytable[(30, "H4")] == 500.00
    # Every size from 30 up to the true grid maximum (36 on 6x6) pays
    # identically -- the product owner's explicit "size 30+ is one tier" rule.
    for symbol in CANDIDATE_A_SYMBOL_WEIGHTS:
        assert config.paytable[(30, symbol)] == config.paytable[(36, symbol)]
    # Non-decreasing within each symbol below the size-30 plateau (some
    # adjacent sizes share a value -- see the 0.10-increment rounding note
    # on CANDIDATE_A_PAYTABLE_STEPS).
    for symbol, steps in CANDIDATE_A_PAYTABLE_STEPS.items():
        breakpoints = sorted(steps)
        values = [steps[bp] for bp in breakpoints]
        assert values == sorted(values)


def test_production_paytable_is_rgs_compatible():
    """Stake Engine RGS lookup-table requirement (utils/rgs_verification.py):
    every non-zero payout must be an exact multiple of 0.10 (bet units)."""
    for steps in CANDIDATE_A_PAYTABLE_STEPS.values():
        for value in steps.values():
            assert value > 0
            assert round(value * 100) % 10 == 0, f"{value} is not a multiple of 0.10"


def test_production_does_not_affect_dev_defaults():
    dev = GameConfig()
    assert dev.progression_rules.thresholds == (5, 10, 15, 20)
    assert dev.n2_temp_wild_count == 0
    assert dev.paytable[(5, "L1")] == 0.1  # DEV rank*(size-4)/10 formula, untouched
    dev_provisional = GameConfig(provisional_paytable=True)
    assert dev_provisional.n2_temp_wild_count == 0
    assert dev_provisional.paytable[(5, "L1")] == 0.10  # PROVISIONAL_PAYTABLE_5_6_7, untouched


def test_production_explicit_overrides_still_win():
    config = GameConfig(production=True, progression_thresholds=(1, 2, 3, 4))
    assert config.progression_rules.thresholds == (1, 2, 3, 4)
    custom_weights = {"L1": 1, "L2": 1, "L3": 1, "L4": 1, "H1": 1, "H2": 1, "H3": 1, "H4": 1}
    config = GameConfig(production=True, symbol_weights=custom_weights)
    strips_symbols_counts = {s: config.reels["DEV"][0].count(s) for s in custom_weights}
    assert len(set(strips_symbols_counts.values())) == 1  # equal weight, not Candidate A's skew


def test_production_access_rates_within_broad_tolerance():
    """Coarse anti-regression guard, not a precision RTP/frequency test (see
    docs/milestones/V0.3.9_candidate_a_calibration.md for the calibrated
    figures: N2 ~1/13.2, N5 ~1/121.4 over 200,000 books)."""
    config = GameConfig(production=True)
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


def test_production_replays_cleanly():
    config = GameConfig(production=True)
    game = GameState(config)
    game.betmode = "base"
    game.criteria = "natural"
    for sim in range(300):
        game.run_spin(sim, simulation_seed=sim + 900000)
        validate_book(game.book.to_json(), config)
