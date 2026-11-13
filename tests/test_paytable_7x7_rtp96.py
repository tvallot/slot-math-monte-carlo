"""V0.3.13/V0.3.14/V0.3.15/V0.3.16 -- product-owner-specified 7x7 paytable,
calibrated for RTP ~96%, then rebalanced, then rebuilt to fix a symbol
hierarchy inconsistency.

`GameConfig(production=True, num_reels=7, num_rows=7)` installs the
KITCHEN_7X7_RTP96_* pairing (see docs/milestones/
PAYTABLE_7x7_RTP_96_KITCHEN_VS_FLY.md, docs/milestones/
V0.3.13_paytable_7x7_rtp96.md, docs/milestones/V0.3.14_kitchen_7x7_rebalance.md,
docs/milestones/V0.3.15_kitchen_7x7_pairs_weights_and_thresholds.md and
docs/milestones/V0.3.16_kitchen_7x7_paytable_rebuild.md), superseding the
V0.3.11 in-house CANDIDATE_7X7_* design: min_cluster_size=5 (not 6).
V0.3.13 installed the product brief's paytable verbatim (except 3
RGS-rounded cells) but reached RTP~=96% only by weakening
`n2_temp_wild_count` (0, not the production baseline 3) and `n4_wild_count`
(1, not the documented baseline 3), and left N5 access at 1-in-299.9.
V0.3.14 restored `n4_wild_count` to its full baseline (3), partially
restored `n2_temp_wild_count` (2 of 3), re-derived `progression_thresholds`
to bring N5 back into a reasonable access band, and rebalanced the
paytable itself (every cell still `>= 50%` of its V0.3.13 value --
`KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V013` is that frozen V0.3.13 reference
point). V0.3.14's own table left an inconsistency in place though:
Beurre/Herbes (L3/L4) paid <= Oignon/Ail (L1/L2) at every breakpoint, which
became a real problem once V0.3.15 locked symbol_weights making L1/L2
(19% each) MORE common than L3/L4 (15% each) -- "rarer must pay more" was
violated. V0.3.15 fixed the weights/thresholds side (pairs_A weights,
`(10, 25, 40, 55)` thresholds, both frozen from V0.3.14 as
`KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS_V014`); V0.3.16 rebuilt the paytable
itself (L1/L2 and L3/L4 swap curves, H1-H4 untouched, then uniformly
rescaled for RTP), frozen from V0.3.14 as
`KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V014`. `num_reels` still defaults to 6
regardless of `production`, so every pre-V0.3.13 caller (including bare
`GameConfig(production=True)` and
`GameConfig(production=True, min_cluster_size=6)`) stays unaffected --
several tests below assert that explicitly.
"""

from kitchen_vs_fly.game_config import (
    KITCHEN_7X7_RTP96_PAYTABLE_STEPS,
    KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V013,
    KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V014,
    KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS,
    KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS_V014,
    GameConfig,
)
from kitchen_vs_fly.gamestate import GameState
from kitchen_vs_fly.validation import validate_book


def test_7x7_rtp96_grid_and_pairing_selection():
    config = GameConfig(production=True, num_reels=7, num_rows=7)
    assert config.num_reels == 7
    assert config.num_rows == [7] * 7
    assert config.min_cluster_size == 5  # auto-selected, not explicitly passed
    assert config.progression_rules.thresholds == (10, 26, 40, 55)  # V0.3.19 (was (10, 25, 40, 55))
    assert config.n2_temp_wild_count == 2
    assert config.n4_wild_count == 3
    assert config.n2_wild_count == 2
    strips_symbols = {symbol for column in config.reels["DEV"] for symbol in column}
    assert strips_symbols == set(KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS)
    assert max(size for size, _symbol in config.paytable) == 49  # 7*7
    assert min(size for size, _symbol in config.paytable) == 5


def test_7x7_rtp96_declared_rtp_within_sdk_3star_limit():
    # V0.3.19 §9.1: the declared RTP (RGS config + optimizer target) must sit
    # between the product owner's 96% floor and the official SDK's 0.967
    # "3-star volatility" limit (utils/rgs_verification.py).
    config = GameConfig(production=True, num_reels=7, num_rows=7)
    assert config.rtp == 0.965
    assert config.bet_modes[0].get_rtp() == 0.965
    assert 0.96 <= config.rtp <= 0.967


def test_7x7_rtp96_weights_are_pairs_a():
    """V0.3.15's locked decision: L1=L2 (19 each) > L3=L4 (15 each), 68%
    low / 32% premium -- restoring the intended frequency hierarchy."""
    assert KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS == {
        "L1": 19, "L2": 19, "L3": 15, "L4": 15, "H1": 11, "H2": 9, "H3": 7, "H4": 5,
    }
    assert KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS["L1"] == KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS["L2"]
    assert KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS["L3"] == KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS["L4"]
    assert (KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS["L1"] > KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS["L3"]
            > KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS["H1"] > KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS["H2"]
            > KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS["H3"] > KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS["H4"])
    # Frozen V0.3.14 reference stays byte-identical to what was actually
    # installed then, for the record and for any future regression check.
    assert KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS_V014 == {
        "L1": 18, "L2": 18, "L3": 10, "L4": 10, "H1": 12, "H2": 11, "H3": 10, "H4": 9,
    }


def test_7x7_rtp96_paytable_matches_installed_constant():
    config = GameConfig(production=True, num_reels=7, num_rows=7)
    for symbol, tiers in KITCHEN_7X7_RTP96_PAYTABLE_STEPS.items():
        for size, value in tiers.items():
            assert config.paytable[size, symbol] == value
    # 30+ all pay the size-30 value, up to the true 7x7 max of 49.
    for symbol in KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS:
        assert config.paytable[49, symbol] == config.paytable[30, symbol]


def test_7x7_rtp96_v013_floor_is_not_an_active_constraint():
    """The V0.3.14 rebalance imposed "no cell below 50% of its V0.3.13
    value" as a constraint SPECIFIC to that exercise (preserving V0.3.13's
    own paytable identity while re-scaling it). V0.3.16 does not carry that
    constraint forward -- its brief never restates it, and it is
    structurally incompatible with fixing the L1/L2-vs-L3/L4 hierarchy
    (swapping which symbol uses which value curve necessarily sends some
    cells below their OLD self's 50% floor). This test documents that
    fact in code rather than leaving it as a silent assumption: it asserts
    the violation exists (not the historical floor itself), matching
    docs/milestones/V0.3.16_kitchen_7x7_paytable_rebuild.md's own count."""
    below_floor = [
        (symbol, size)
        for symbol, tiers in KITCHEN_7X7_RTP96_PAYTABLE_STEPS.items()
        for size, value in tiers.items()
        if value < 0.5 * KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V013[symbol][size] - 1e-9
    ]
    assert len(below_floor) == 20, (
        f"expected exactly the 20 L1/L2 cells (size>=10) documented as below the historical "
        f"V0.3.13 floor, got {len(below_floor)}: {below_floor}"
    )
    assert all(symbol in ("L1", "L2") for symbol, _size in below_floor)
    # V0.3.14's own table (still frozen here) DID respect that floor by
    # construction -- confirms the floor check itself still works and V0.3.14
    # wasn't silently broken by this edit.
    for symbol, tiers in KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V014.items():
        for size, value in tiers.items():
            assert value >= 0.5 * KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V013[symbol][size] - 1e-9


def test_7x7_rtp96_paytable_is_rgs_compatible():
    for symbol, steps in KITCHEN_7X7_RTP96_PAYTABLE_STEPS.items():
        breakpoints = sorted(steps)
        values = [steps[bp] for bp in breakpoints]
        assert values == sorted(values), f"{symbol} steps not non-decreasing"
        for value in values:
            assert value > 0
            assert round(value * 100) % 10 == 0, f"{symbol}: {value} is not a multiple of 0.10"


def test_7x7_rtp96_symbol_hierarchy_preserved():
    """V0.3.16's whole purpose: at every breakpoint, value must increase
    with rarity -- Oignon=Ail (L1=L2) < Beurre=Herbes (L3=L4) < Couteau
    (H1) < Plat (H2) < Gants (H3) < Poulet roti (H4) -- matching the
    frequency order pairs_A installs (L1=L2 > L3=L4 > H1 > H2 > H3 > H4,
    see test_7x7_rtp96_weights_are_pairs_a). This is the exact opposite
    direction from V0.3.14's own table, which had L1/L2 >= L3/L4
    everywhere (see docs/milestones/V0.3.16_kitchen_7x7_paytable_rebuild.md)."""
    ties = []
    for size in KITCHEN_7X7_RTP96_PAYTABLE_STEPS["H1"]:
        assert KITCHEN_7X7_RTP96_PAYTABLE_STEPS["H4"][size] > KITCHEN_7X7_RTP96_PAYTABLE_STEPS["H3"][size] \
            > KITCHEN_7X7_RTP96_PAYTABLE_STEPS["H2"][size] > KITCHEN_7X7_RTP96_PAYTABLE_STEPS["H1"][size]
        assert KITCHEN_7X7_RTP96_PAYTABLE_STEPS["L1"][size] == KITCHEN_7X7_RTP96_PAYTABLE_STEPS["L2"][size]
        assert KITCHEN_7X7_RTP96_PAYTABLE_STEPS["L3"][size] == KITCHEN_7X7_RTP96_PAYTABLE_STEPS["L4"][size]
        l1l2, l3l4 = KITCHEN_7X7_RTP96_PAYTABLE_STEPS["L1"][size], KITCHEN_7X7_RTP96_PAYTABLE_STEPS["L3"][size]
        assert l1l2 <= l3l4, f"size {size}: L1/L2={l1l2} > L3/L4={l3l4}, hierarchy violated"
        assert l3l4 < KITCHEN_7X7_RTP96_PAYTABLE_STEPS["H1"][size]
        if l1l2 == l3l4:
            ties.append(size)
    # Exactly one unavoidable tie: both L1/L2 and L3/L4 pinned at the 0.10
    # RGS floor at size 5 -- same kind of exception V0.3.14 itself accepted
    # for a different cell (see that milestone's own paytable comment).
    assert ties == [5], f"expected exactly one tie at size 5, got {ties}"


def test_7x7_rtp96_does_not_affect_default_or_min6_or_6x6_production():
    config5 = GameConfig(production=True)
    assert config5.num_reels == 6
    assert config5.progression_rules.thresholds == (20, 40, 60, 80)
    assert config5.n4_wild_count == 3
    assert config5.n2_temp_wild_count == 3
    config6 = GameConfig(production=True, min_cluster_size=6)
    assert config6.num_reels == 6
    assert config6.progression_rules.thresholds == (16, 32, 48, 64)
    assert config6.n4_wild_count == 3
    # The 7x7 declared-RTP change leaves 6x6 production at its measured
    # 0.969 (knowingly above the SDK 3-star limit, open decision) and DEV at 0.96.
    assert config5.rtp == 0.969
    assert config6.rtp == 0.969
    assert GameConfig().rtp == 0.96


def test_7x7_rtp96_explicit_overrides_still_win():
    config = GameConfig(
        production=True, num_reels=7, num_rows=7, min_cluster_size=6,
        progression_thresholds=(5, 10, 15, 20),
    )
    assert config.num_reels == 7
    assert config.min_cluster_size == 6  # explicit override, not auto-selected 5
    assert config.progression_rules.thresholds == (5, 10, 15, 20)
    # An explicit min_cluster_size=6 override still uses the 7x7 RTP96
    # paytable values/weights, but _step_paytable's own min_size follows
    # config.min_cluster_size, so size-5 breakpoints are simply never
    # generated (not merely unused) -- size 6 becomes the floor instead.
    assert min(size for size, _symbol in config.paytable) == 6
    assert (5, "H1") not in config.paytable
    assert config.paytable[6, "H1"] == KITCHEN_7X7_RTP96_PAYTABLE_STEPS["H1"][6]


def test_7x7_rtp96_access_rates_within_broad_tolerance():
    """Coarse anti-regression guard, not a precision RTP/frequency test (see
    docs/milestones/V0.3.16_kitchen_7x7_paytable_rebuild.md for the
    calibrated figures: RTP ~96.6%, N2 ~1-in-12.2, N5 ~1-in-117 over large
    samples)."""
    config = GameConfig(production=True, num_reels=7, num_rows=7)
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
    assert 0.02 * n_books <= n2 <= 0.12 * n_books, f"N2 access rate out of broad range: {n2}/{n_books}"
    assert 0.001 * n_books <= n5 <= 0.015 * n_books, f"N5 access rate out of broad range: {n5}/{n_books}"


def test_7x7_rtp96_replays_cleanly():
    config = GameConfig(production=True, num_reels=7, num_rows=7)
    game = GameState(config)
    game.betmode = "base"
    game.criteria = "natural"
    for sim in range(300):
        game.run_spin(sim, simulation_seed=sim + 980000)
        validate_book(game.book.to_json(), config)
