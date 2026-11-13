"""V0.3.20 -- publication lookup-table weights (games/knight_progression/publication.py)."""

from fractions import Fraction

import pytest

from kitchen_vs_fly.publication import (
    UINT64_MAX,
    published_statistics,
    write_weighted_lookup,
    zero_win_weights,
)
from utils.analysis.distribution_functions import calculate_rtp


def test_exact_target_rtp_when_sample_is_above_target():
    payouts = [0] * 670 + [30] * 250 + [200] * 70 + [10000] * 10  # sample RTP 1.1
    w, z = zero_win_weights(payouts, 0.965)
    stats = published_statistics(payouts, w, z)
    assert stats["rtp"] == Fraction(965, 1000)
    assert z > w  # more zero-win mass pulls RTP down; hit rate falls


def test_exact_target_rtp_when_sample_is_below_target():
    payouts = [0] * 690 + [30] * 250 + [200] * 50 + [5000] * 10  # sample RTP 0.675
    w, z = zero_win_weights(payouts, 0.965)
    assert published_statistics(payouts, w, z)["rtp"] == Fraction(965, 1000)
    assert z < w  # less zero-win mass raises RTP; hit rate rises


def test_winning_outcomes_keep_their_simulated_relative_frequencies():
    payouts = [0] * 600 + [30] * 300 + [200] * 90 + [9000] * 10
    w, z = zero_win_weights(payouts, 0.965)
    weights = [w if p else z for p in payouts]
    wins = [(p, wt) for p, wt in zip(payouts, weights) if p]
    assert len({wt for _, wt in wins}) == 1  # one common weight for every winning book
    assert len({wt for p, wt in zip(payouts, weights) if not p}) == 1


def test_official_calculate_rtp_agrees():
    payouts = [0] * 680 + [30] * 240 + [200] * 70 + [12000] * 10
    w, z = zero_win_weights(payouts, 0.965)
    dist = {}
    for p in payouts:
        weight = w if p else z
        dist[p / 100] = dist.get(p / 100, 0) + weight
    assert abs(calculate_rtp(dist, 1.0, sum(dist.values())) - 0.965) < 1e-12


def test_impossible_targets_fail_closed():
    with pytest.raises(ValueError):
        zero_win_weights([0, 0, 50], 0.965)  # winning mean 0.5 < 0.965
    with pytest.raises(ValueError):
        zero_win_weights([0, 0, 0], 0.965)


def test_weighted_lookup_file_roundtrip(tmp_path):
    base = tmp_path / "lookUpTable_base.csv"
    rows = [(i, 1, p) for i, p in enumerate([0] * 60 + [40] * 30 + [300] * 9 + [8000])]
    base.write_text("".join(f"{i},{w},{p}\n" for i, w, p in rows))
    out = tmp_path / "lookUpTable_base_0.csv"
    result = write_weighted_lookup(base, out, 0.965)
    assert result["rtp"] == Fraction(965, 1000)
    assert result["total_weight"] <= UINT64_MAX
    written = [tuple(map(int, line.split(","))) for line in out.read_text().splitlines()]
    assert [(i, p) for i, _, p in written] == [(i, p) for i, _, p in rows]
    assert all(w > 0 for _, w, _ in written)


def test_non_unit_input_is_rejected(tmp_path):
    base = tmp_path / "lookUpTable_base.csv"
    base.write_text("0,2,0\n1,1,500\n")
    with pytest.raises(ValueError):
        write_weighted_lookup(base, tmp_path / "out.csv", 0.965)


# --- V0.3.21 class weights ---------------------------------------------------

from kitchen_vs_fly.publication import class_weights  # noqa: E402


def _classes():
    natural = [0] * 6000 + [30] * 3000 + [200] * 900 + [9000] * 100
    golden = [150_000] * 40 + [300_000] * 10 + [650_000] * 5
    return natural, golden


def test_class_weights_hit_the_target_rtp_and_the_class_masses():
    natural, golden = _classes()
    cap = 1_000_000
    result = class_weights(natural, golden, cap_books=8, golden_mass=Fraction(1, 500_000),
                           cap_mass=Fraction(1, 20_000_000), cap_payout=cap, target_rtp=0.965)
    assert abs(result["rtp"] - Fraction(965, 1000)) < Fraction(1, 10**9)
    assert abs(result["cap_probability"] - Fraction(1, 20_000_000)) < Fraction(1, 20_000_000) / 1000
    assert abs(result["golden_probability"] - Fraction(1, 500_000)) < Fraction(1, 500_000) / 1000
    assert result["total_weight"] <= UINT64_MAX and result["cap_weight"] >= 1


def test_physical_book_count_is_not_the_probability():
    natural, golden = _classes()
    a = class_weights(natural, golden, cap_books=2, golden_mass=Fraction(1, 500_000),
                      cap_mass=Fraction(1, 20_000_000), cap_payout=1_000_000, target_rtp=0.965)
    b = class_weights(natural, golden, cap_books=50, golden_mass=Fraction(1, 500_000),
                      cap_mass=Fraction(1, 20_000_000), cap_payout=1_000_000, target_rtp=0.965)
    assert abs(a["cap_probability"] - b["cap_probability"]) < Fraction(1, 20_000_000) / 500
    assert a["cap_weight"] > b["cap_weight"]  # fewer books -> each one heavier


def test_class_weights_reject_impossible_inputs():
    natural, golden = _classes()
    with pytest.raises(ValueError):
        class_weights(natural, golden, cap_books=0, golden_mass=Fraction(1, 10**6),
                      cap_mass=Fraction(1, 10**7), cap_payout=1_000_000, target_rtp=0.965)
    with pytest.raises(ValueError):  # classes alone exceed the RTP target
        class_weights(natural, golden, cap_books=5, golden_mass=Fraction(1, 10),
                      cap_mass=Fraction(1, 10), cap_payout=1_000_000, target_rtp=0.965)


# --- V0.3.21 final: weighting by P(Golden | N5) ------------------------------

from kitchen_vs_fly.publication import class_weights_by_n5_rate  # noqa: E402


def _n5_classes():
    # 4,000 natural books, 120 of them reaching N5 (all winning).
    natural = [0] * 2600 + [30] * 1200 + [200] * 80 + [9000] * 120
    golden = [150_000] * 40 + [300_000] * 10 + [650_000] * 5
    return natural, 120, golden


def test_golden_given_n5_is_exact_and_rtp_is_hit():
    natural, n5, golden = _n5_classes()
    r = class_weights_by_n5_rate(natural, n5, golden, cap_books=8, golden_given_n5=Fraction(1, 10_000),
                                 cap_given_golden=Fraction(1_954_668, 20_000_000), cap_payout=1_000_000,
                                 target_rtp=0.965)
    assert abs(r["golden_given_n5"] - Fraction(1, 10_000)) < Fraction(1, 10_000) / 10**5
    assert abs(r["cap_given_golden"] - Fraction(1_954_668, 20_000_000)) < Fraction(1, 10**5)
    assert abs(r["rtp"] - Fraction(965, 1000)) < Fraction(1, 10**9)
    assert r["total_weight"] <= UINT64_MAX and r["win_weight"] == 2**36


def test_only_the_zero_win_weight_absorbs_the_extra_golden_rtp():
    natural, n5, golden = _n5_classes()
    kw = dict(cap_books=8, cap_given_golden=Fraction(1_954_668, 20_000_000), cap_payout=1_000_000, target_rtp=0.965)
    low = class_weights_by_n5_rate(natural, n5, golden, golden_given_n5=Fraction(1, 16_634), **kw)
    high = class_weights_by_n5_rate(natural, n5, golden, golden_given_n5=Fraction(1, 10_000), **kw)
    assert low["win_weight"] == high["win_weight"]            # natural winning books untouched
    assert high["zero_weight"] > low["zero_weight"]           # more zero-win mass pays for more Golden
    assert high["cap_probability"] > low["cap_probability"]   # the cap follows Golden (accepted)


def test_n5_rate_weighting_rejects_bad_inputs():
    natural, n5, golden = _n5_classes()
    with pytest.raises(ValueError):
        class_weights_by_n5_rate(natural, n5, golden, 0, Fraction(1, 10_000), Fraction(1, 10), 1_000_000, 0.965)
    with pytest.raises(ValueError):
        class_weights_by_n5_rate(natural, n5, golden, 5, Fraction(0), Fraction(1, 10), 1_000_000, 0.965)
