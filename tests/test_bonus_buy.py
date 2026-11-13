"""V0.3.22 Bonus Buy (mode "bonus_buy", cost 100x, B1 correction).

A purchased round is a complete natural round N1 -> N5. These tests pin the
mode configuration, the `force_n5` acceptance rule, the exact integer
weights (Golden exactly 1/10,000, B1 monotone, RTP on target) and the
guarantee that the base game is untouched.
"""

import hashlib
import json
import math
import random
from fractions import Fraction
from pathlib import Path

import pytest

from kitchen_vs_fly import game_config as gc
from kitchen_vs_fly.gamestate import GameState
from kitchen_vs_fly.publication import UINT64_MAX, bonus_buy_weights, solve_b1_lambda
from kitchen_vs_fly.validation import validate_book
from src.state.run_sims import string_to_int

LIBRARY = Path(gc.__file__).resolve().parent / "library" / "publish_files"
# Installed base library fingerprints (V0.3.21 final, verified 2026-10-12).
BASE_BOOKS_SHA256 = "7a30ce2d3a391e3be40058a9eb6b27e4bc24987d2f6b7bcd88740e8afd3ee350"
BASE_LOOKUP_SHA256 = "33ff002579f641d02449b000cdc6be6182c22230e5304ff187cb8d0dd090b6ed"


def distribution_signature(mode):
    return [(d.get_criteria(), d.get_quota(), d.get_fixed_amt(), d._conditions) for d in mode.get_distributions()]


def test_default_production_config_has_only_the_base_mode():
    assert [m.get_name() for m in gc.production_config().bet_modes] == ["base"]


def test_bonus_buy_mode_is_appended_after_an_unchanged_base_mode():
    plain, with_buy = gc.production_config(), gc.production_config(bonus_buy_books=100_000)
    assert [m.get_name() for m in with_buy.bet_modes] == ["base", "bonus_buy"]
    base_a, base_b = plain.bet_modes[0], with_buy.bet_modes[0]
    assert (base_a.get_cost(), base_a.get_rtp(), base_a.get_wincap(), base_a.get_buybonus()) == \
           (base_b.get_cost(), base_b.get_rtp(), base_b.get_wincap(), base_b.get_buybonus())
    assert distribution_signature(base_a) == distribution_signature(base_b)
    buy = with_buy.bet_modes[1]
    assert (buy.get_cost(), buy.get_rtp(), buy.get_wincap(), buy.get_buybonus()) == (100.0, 0.965, 10_000.0, True)
    (dist,) = buy.get_distributions()
    assert (dist.get_criteria(), dist.get_fixed_amt(), dist._conditions["force_n5"]) == ("buy_n5", 100_000, True)


def test_force_n5_repeats_until_a_normal_boss_round():
    config = gc.production_config(bonus_buy_books=1)
    state = GameState(config)
    state.betmode, state.criteria = "bonus_buy", "buy_n5"
    state.reset_book()
    state.check_repeat()
    assert state.repeat, "no boss yet: the round must be rejected"
    state.n5_boss_active = True
    state.check_repeat()
    assert not state.repeat, "an N5 round without Golden is accepted"
    state.n5_boss_active, state.n5_boss_defeated = False, True
    state.check_repeat()
    assert not state.repeat
    state.golden_active = True
    state.check_repeat()
    assert state.repeat, "a Golden round is never a NORMAL purchase"


def test_base_criteria_never_use_force_n5():
    config = gc.production_config(golden_books=4000, wincap_books=250, bonus_buy_books=1)
    for dist in config.bet_modes[0].get_distributions():
        assert "force_n5" not in dist._conditions


@pytest.mark.parametrize("seed", [0, 1, 7, 236, 1193])
def test_base_rounds_are_identical_with_or_without_the_buy_mode(seed):
    books = []
    for kwargs in ({}, {"bonus_buy_books": 100_000}):
        config = gc.production_config(**kwargs)
        state = GameState(config)
        state.betmode, state.criteria = "base", "natural"
        state.run_spin(seed, simulation_seed=seed)
        books.append(json.dumps(state.book.to_json()))
    assert books[0] == books[1]


@pytest.mark.skipif(not (LIBRARY / "books_base.jsonl.zst").exists(), reason="installed base library not present")
def test_installed_base_files_are_unchanged():
    digest = lambda p: hashlib.sha256((LIBRARY / p).read_bytes()).hexdigest()
    assert digest("books_base.jsonl.zst") == BASE_BOOKS_SHA256
    assert digest("lookUpTable_base_0.csv") == BASE_LOOKUP_SHA256


def test_buy_n5_round_is_a_complete_rounds_n1_to_n5():
    config = gc.production_config(bonus_buy_books=1)
    state = GameState(config)
    state.betmode, state.criteria = "bonus_buy", "buy_n5"
    state.run_spin(3, simulation_seed=string_to_int("buy_n5") + 3)
    book = state.book.to_json()
    validate_book(book, config)
    kinds = [e["type"] for e in book["events"]]
    assert kinds[0] == "reveal" and "goldenFlyStarted" not in kinds
    assert [e["newLevel"] for e in book["events"] if e["type"] == "levelUp"][:4] == [2, 3, 4, 5]
    assert kinds.index("bossTriggered") > max(i for i, k in enumerate(kinds) if k == "levelUp")
    assert "bossDefeated" in kinds


def synthetic_classes():
    rng = random.Random(1)
    normal = [int(round(math.exp(rng.gauss(4.5, 0.7)) * 10)) * 10 for _ in range(3000)]
    golden = [rng.randrange(20_000, 900_000, 10) for _ in range(300)]
    return normal, golden


def test_bonus_buy_weights_are_exact_integers():
    normal, golden = synthetic_classes()
    result = bonus_buy_weights(normal, golden, 40, 1_000_000)
    assert result["golden_probability"] == Fraction(1, 10_000)  # exactly, after integer conversion
    assert result["cap_given_golden"] == Fraction(1_954_668, 20_000_000)
    assert abs(result["rtp"] - Fraction("0.965")) < Fraction(1, 10**11)
    weights = result["normal_weights"] + result["golden_weights"] + result["cap_weights"]
    assert all(type(w) is int and w > 0 for w in weights)
    assert result["total_weight"] == sum(weights) <= UINT64_MAX


def test_b1_weight_decreases_smoothly_with_the_payout():
    normal, golden = synthetic_classes()
    result = bonus_buy_weights(normal, golden, 40, 1_000_000)
    assert result["lambda"] > 0, "the synthetic mean exceeds the target: high payouts must be down-weighted"
    ordered = sorted(zip(normal, result["normal_weights"]))
    weights = [w for _, w in ordered]
    # exp(-lambda*y) is monotone: allow only the +-1 integer rounding of the apportionment
    assert all(b <= a + 1 for a, b in zip(weights, weights[1:]))
    ratio = weights[-1] / weights[0]
    assert 0 < ratio < 1


def test_solve_b1_lambda_hits_the_target_mean():
    normal, golden = synthetic_classes()
    lam, ybar = solve_b1_lambda(normal, golden, 1_000_000, Fraction(1, 10_000), Fraction(1_954_668, 20_000_000), 0.965, 100.0)
    y = [v / 100 for v in normal]
    f = [math.exp(-lam * (v - ybar)) for v in y]
    mean_golden = ((1 - 0.0977334) * sum(golden) / len(golden) + 0.0977334 * 1_000_000) / 100
    mixture = (1 - 1e-4) * sum(a * b for a, b in zip(f, y)) / sum(f) + 1e-4 * mean_golden
    assert mixture == pytest.approx(96.5, abs=1e-9)
