"""V0.3.21 Golden Mega Fly: a rare N5 state whose multiplier applies, as the
SDK's global_multiplier, only to the dedicated evaluation of each boss size.
"""

import pytest

from kitchen_vs_fly.game_config import GameConfig, production_config
from kitchen_vs_fly.gamestate import GameState
from kitchen_vs_fly.validation import validate_book

N5_SEEDS = [236, 163, 576, 664, 1193]
LADDER = [(10, 1), (300, 1)]


def make(criteria="natural", **kwargs):
    config = GameConfig(production=True, num_reels=7, num_rows=7, **kwargs)
    state = GameState(config)
    state.betmode, state.criteria = "base", criteria
    return config, state


def play(config, state, seed):
    state.run_spin(seed, simulation_seed=seed)
    book = state.book.to_json()
    validate_book(book, config)
    return book


def kinds(book):
    return [e["type"] for e in book["events"]]


@pytest.mark.parametrize("seed", N5_SEEDS)
def test_golden_disabled_books_are_identical_to_the_plain_game(seed):
    plain_config, plain = make()
    off_config, off = make(golden_ladder=LADDER, golden_trigger_probability=0.0)
    assert not off_config.golden_enabled
    assert play(plain_config, plain, seed) == play(off_config, off, seed)


@pytest.mark.parametrize("seed", N5_SEEDS)
def test_forced_golden_emits_one_event_and_multiplies_only_boss_evaluations(seed):
    config, state = make("golden", wincap=10000, golden_ladder=LADDER, golden_books=1)
    book = play(config, state, seed)
    k = kinds(book)
    assert k.count("goldenFlyStarted") == 1
    assert k[k.index("bossTriggered") + 1] == "goldenFlyStarted"
    golden = book["events"][k.index("goldenFlyStarted")]
    multiplier = golden["multiplier"]
    assert multiplier in (10, 300) and golden["scope"] == "bossEvaluations"
    # Walk the round: a winInfo carries globalMultiplier iff it is the one
    # dedicated evaluation of a boss size (the first winInfo after the size
    # appeared); at most 3 such evaluations exist.
    dedicated = 0
    awaiting = False
    for event in book["events"]:
        if event["type"] in ("bossTriggered", "bossShrunk"):
            awaiting = True
        elif event["type"] == "bossDefeated":
            awaiting = False
        elif event["type"] == "winInfo":
            if awaiting:
                dedicated += 1
                assert all(w["globalMultiplier"] == multiplier for w in event["wins"])
                awaiting = False
            else:
                assert all("globalMultiplier" not in w for w in event["wins"])
    assert 1 <= dedicated <= 3
    assert book["payoutMultiplier"] == min(book["events"][-1]["amount"], 1_000_000)


@pytest.mark.parametrize("seed", N5_SEEDS[:3])
def test_forced_golden_is_deterministic_by_seed(seed):
    config, state = make("golden", wincap=10000, golden_ladder=LADDER, golden_books=1)
    assert play(config, state, seed) == play(config, state, seed)


def test_golden_pays_at_least_the_ordinary_n5_round():
    """Same seed, same boards up to the trigger: the multiplier can only add."""
    plain_config, plain = make(wincap=10000)
    config, state = make("golden", wincap=10000, golden_ladder=LADDER, golden_books=1)
    ordinary = play(plain_config, plain, 576)["payoutMultiplier"]
    assert play(config, state, 576)["payoutMultiplier"] >= ordinary


def test_probability_roll_zero_never_and_one_always():
    config, state = make(wincap=10000, golden_ladder=[(10, 1)], golden_trigger_probability=1.0)
    assert config.golden_enabled
    always = play(config, state, 576)
    assert kinds(always).count("goldenFlyStarted") == 1
    config, state = make(wincap=10000, golden_ladder=[(10, 1)], golden_trigger_probability=0.0)
    assert "goldenFlyStarted" not in kinds(play(config, state, 576))


def test_wincap_criteria_produces_a_real_exactly_capped_simulation():
    """SDK win_criteria + repeat: the book is a genuine simulation whose final
    win is exactly 10,000x (event amounts, finalWin and payout all agree)."""
    config, state = make("wincap", wincap=10000, golden_ladder=[(300, 1)], wincap_books=1)
    book = play(config, state, 0)
    assert book["criteria"] == "wincap"
    assert book["payoutMultiplier"] == 1_000_000
    assert "wincap" in kinds(book) and kinds(book).count("goldenFlyStarted") == 1
    assert book["events"][-1]["type"] == "finalWin" and book["events"][-1]["amount"] == 1_000_000
    assert round(book["baseGameWins"] * 100) == 1_000_000


def test_natural_criteria_never_rolls_golden_even_with_a_ladder():
    config, state = make("natural", wincap=10000, golden_ladder=LADDER, golden_books=1)
    for seed in N5_SEEDS:
        assert "goldenFlyStarted" not in kinds(play(config, state, seed))


def test_production_config_is_the_approved_v0321_design():
    from fractions import Fraction
    from kitchen_vs_fly.game_config import (
        CAP_GIVEN_GOLDEN, GOLDEN_LADDER, GOLDEN_LADDER_PROBABILITIES, GOLDEN_PROBABILITY_PER_N5,
        PRODUCTION_GOLDEN_BOOKS, PRODUCTION_NATURAL_BOOKS, PRODUCTION_WINCAP_BOOKS)
    config = production_config()
    assert config.wincap == 10_000.0 and config.bet_modes[0].get_wincap() == 10_000.0
    assert [m for m, _ in config.golden_ladder] == [25, 40, 600] == [m for m, _ in GOLDEN_LADDER]
    assert [p for _, p in GOLDEN_LADDER_PROBABILITIES] == [Fraction(36, 100), Fraction(55, 100), Fraction(9, 100)]
    assert sum(p for _, p in GOLDEN_LADDER_PROBABILITIES) == 1
    # Golden never rolls inside the natural distribution; it is class-weighted.
    assert not config.golden_enabled and config.golden_trigger_probability == 0.0
    assert [d.get_criteria() for d in config.bet_modes[0].get_distributions()] == ["natural"]
    # FINAL (supersedes 1 / 16,634 N5 and the 1-in-20M cap): exactly 1 Golden per 10,000 N5.
    assert config.golden_probability_per_n5 == GOLDEN_PROBABILITY_PER_N5 == Fraction(1, 10_000)
    assert config.cap_given_golden == CAP_GIVEN_GOLDEN == Fraction(1_954_668, 20_000_000)
    assert PRODUCTION_NATURAL_BOOKS + PRODUCTION_GOLDEN_BOOKS + PRODUCTION_WINCAP_BOOKS == 510_000
    full = production_config(PRODUCTION_GOLDEN_BOOKS, PRODUCTION_WINCAP_BOOKS)
    assert [(d.get_criteria(), d.get_fixed_amt()) for d in full.bet_modes[0].get_distributions()] == [
        ("natural", None), ("golden", 4000), ("wincap", 250)]


# Real forced-Golden rounds (production config, criteria "golden") whose three
# boss sizes all paid and which did not cap: seed -> (multiplier, payout x).
GOLDEN_ROUNDS = {3: (25, 4407.0), 1: (40, 3402.9), 547: (600, 7248.6)}


@pytest.mark.parametrize("seed", sorted(GOLDEN_ROUNDS))
def test_one_multiplier_covers_all_three_boss_sizes_and_ends_with_n5(seed):
    multiplier, payout = GOLDEN_ROUNDS[seed]
    config = production_config(4000, 250)
    state = GameState(config)
    state.betmode, state.criteria = "base", "golden"
    book = play(config, state, seed)
    events = book["events"]
    golden = [e for e in events if e["type"] == "goldenFlyStarted"]
    assert len(golden) == 1 and golden[0]["multiplier"] == multiplier and golden[0]["scope"] == "bossEvaluations"
    assert events[[e["type"] for e in events].index("bossTriggered") + 1]["type"] == "goldenFlyStarted"
    state_by_size, size, awaiting, after_boss = {}, None, False, False
    for event in events:
        kind = event["type"]
        if kind == "bossTriggered":
            size, awaiting = 3, True
        elif kind == "bossShrunk":
            size, awaiting = event["newSize"], True
        elif kind == "bossDefeated":
            after_boss, awaiting = True, False
        elif kind == "winInfo":
            multipliers = {w.get("globalMultiplier") for w in event["wins"]}
            if awaiting:
                state_by_size[size] = multipliers
                awaiting = False
            else:
                assert multipliers == {None}  # before, between and after the boss: never Golden
    assert state_by_size == {3: {multiplier}, 2: {multiplier}, 1: {multiplier}}
    assert after_boss
    assert book["payoutMultiplier"] == round(payout * 100) == events[-1]["amount"]
    assert sum(e["totalWin"] for e in events if e["type"] == "winInfo") == book["payoutMultiplier"]
    assert "wincap" not in [e["type"] for e in events]


def test_normal_n5_has_no_golden_event_and_no_global_multiplier():
    config = production_config()
    state = GameState(config)
    state.betmode, state.criteria = "base", "natural"
    for seed in N5_SEEDS:
        book = play(config, state, seed)
        assert "goldenFlyStarted" not in kinds(book) and kinds(book).count("bossTriggered") == 1
        assert all("globalMultiplier" not in w for e in book["events"] if e["type"] == "winInfo" for w in e["wins"])


@pytest.mark.filterwarnings("ignore::UserWarning")  # rejection sampling: ~2,000 repeats expected
def test_production_wincap_book_is_a_genuine_exact_10000x_simulation():
    config = production_config(4000, 250)
    state = GameState(config)
    state.betmode, state.criteria = "base", "wincap"
    book = play(config, state, 0)
    events = book["events"]
    assert book["payoutMultiplier"] == 1_000_000 and events[-1]["amount"] == 1_000_000
    wincap_at = [e["type"] for e in events].index("wincap")
    # The cap is reached by a real winInfo; nothing happens after the cap
    # except the closing bookkeeping events.
    assert events[wincap_at - 1]["type"] == "updateTumbleWin"
    assert sum(e["totalWin"] for e in events if e["type"] == "winInfo") >= 1_000_000
