"""Development mathematics by default. The Kitchen vs Fly production
configuration is `production_config()` (defined at the bottom of this module),
i.e. `GameConfig(production=True, num_reels=7, num_rows=7)`: the 7x7 V0.3.19
pairing -- thresholds (10, 26, 40, 55), Fly mini-bonus enabled (0.057143,
2-3 WFLY), pairs_A weights, the V0.3.18 paytable, N5 always completing (V0.3.21), Golden Mega Fly
(ladder 25/40/600), wincap 10,000x, declared RTP 0.965 (see docs/milestones/V0.3.19_kitchen_7x7_fly_10_26_40_55_validation.md).
Bare `GameConfig(production=True)` is the historical 6x6 Candidate A (V0.3.9),
kept for history and tests only, no longer used by the game or frontend."""

from fractions import Fraction
from random import Random

from src.config.betmode import BetMode
from src.config.config import Config
from src.config.distributions import Distribution
from kitchen_vs_fly.progression import ProgressionRules


# V0.3.8 provisional paytable (bet multipliers), approved ONLY for cluster
# sizes 5/6/7 (see docs/milestones/
# V0.3.8_symbol_frequencies_fly_minibonus_20_40_60_80.md). Keyed by the
# technical symbol code (L1-L4 low, H1-H4 premium); see
# symbol_display_names below for the narrative role each code plays in this
# table (Onion/Garlic cheapest lows, Roast Chicken priciest premium). Not
# installed as the default paytable — see GameConfig(provisional_paytable=)
# below — so every pre-V0.3.8 caller keeps the old DEV rank*(size-4)/10
# formula byte-for-byte.
PROVISIONAL_PAYTABLE_5_6_7 = {
    "L1": {5: 0.10, 6: 0.20, 7: 0.30},    # Onion
    "L2": {5: 0.10, 6: 0.20, 7: 0.30},    # Garlic
    "L3": {5: 0.15, 6: 0.30, 7: 0.50},    # Butter
    "L4": {5: 0.15, 6: 0.30, 7: 0.50},    # Thyme / herbs
    "H1": {5: 0.50, 6: 1.00, 7: 1.50},    # Chef knife
    "H2": {5: 1.00, 6: 2.00, 7: 3.00},    # Roasting dish / pan
    "H3": {5: 1.50, 6: 3.00, 7: 5.00},    # Oven gloves
    "H4": {5: 3.00, 6: 6.00, 7: 10.00},   # Roast chicken
}


def _provisional_paytable(symbols, max_size):
    """Sizes 5/6/7 are PROVISIONAL_PAYTABLE_5_6_7 verbatim (approved for
    V0.3.8 experimentation only). Every size above 7 up to a full-board
    cluster (`max_size`) still needs SOME entry, or
    src.calculations.cluster silently pays 0 for it instead of erroring
    (see the num_rows generalization note on the old formula below) —
    which would silently bias any V0.3.8 measurement that happens to
    produce a large low-symbol cluster on the bigger 6x7 board. This
    continuation is an explicitly TEMPORARY, UNAPPROVED extrapolation for
    simulation purposes only (same brief, section 9/14): each size above 7
    repeats that symbol's own 6->7 increment. Do not read the 8+ values as
    a design decision — only 5/6/7 are approved."""
    table = {}
    for symbol in symbols:
        tiers = PROVISIONAL_PAYTABLE_5_6_7[symbol]
        step = tiers[7] - tiers[6]
        for size in range(5, 8):
            table[size, symbol] = tiers[size]
        for size in range(8, max_size + 1):
            table[size, symbol] = round(table[size - 1, symbol] + step, 10)
    return table


# V0.3.9 "Candidate A" production configuration (see docs/milestones/
# V0.3.9_candidate_a_calibration.md): the individual-symbol weights chosen
# for the 20/40/60/80 progression schedule, ~1/15 N2 access and ~1/121 N5
# access (measured over 200,000 books, replay-validated). Aggregate 70% low
# / 30% premium, with onion/garlic weighted well above butter/herbs among
# the lows, per explicit product direction. Only installed when
# `GameConfig(production=True)`; every caller that omits it is unaffected.
CANDIDATE_A_SYMBOL_WEIGHTS = {
    "L1": 29, "L2": 29, "L3": 6, "L4": 6,
    "H1": 10, "H2": 8, "H3": 7, "H4": 5,
}

# V0.3.9 production paytable (bet multipliers): a step function of cluster
# size, not a continuous curve — each key is the size at which that value
# starts applying, up to (but not including) the next key. Anchored on the
# real per-symbol base values fixed by the product owner at size 5
# (identical to PROVISIONAL_PAYTABLE_5_6_7); sizes 20-24/25-29/30+ are the
# product owner's own values verbatim (a rare "jackpot" tier that measured
# 0 hits in 200,000 books, so it costs nothing in RTP terms and is not
# reduced); sizes 5-19 are scaled down from the product owner's first draft
# to bring RTP from >300% into the 96-97% target band. Every value is an
# exact multiple of 0.10 (a Stake Engine RGS lookup-table requirement,
# `utils/rgs_verification.py::verify_lookup_format`: non-zero payouts must
# be >=10 and a multiple of 10 in hundredths-of-bet units) — this rounding
# pass replaced the finer 0.05-increment draft that `execute_all_tests`
# rejected, and is why some adjacent sizes share the same value (a flat
# step, not strictly increasing) rather than being forced apart by 0.05.
# Size 30 and up (through the grid's true maximum cluster size) all pay the
# "30" entry — see _step_paytable.
#
# V0.3.10 reward-curve reshape ("Candidate M", see docs/milestones/
# V0.3.10_n5_rtp_contribution_and_reward_curve_review.md §5): a follow-up
# review found 52.3% of RTP was paid out by the smallest cluster tier
# (size 5-9) alone, with size-20+ connections contributing almost nothing
# (3.9%) even though rare — the reward curve read as "low" for
# medium/large connections because it was. Sizes 5-9 scaled down (~×0.70)
# and sizes 10-19 scaled up (~×1.35) per symbol, then rescaled/rounded back
# into the 96-97% RTP band using a (symbol, size) -> sum(clusterMultiplier)
# histogram captured once over 200,000 books (same recompute-without-resimulating
# technique as V0.3.9 §5) so many candidate curves could be compared
# without re-running the simulator each time. Sizes 20/25/30 are
# deliberately excluded from the rescale and left byte-identical to the
# V0.3.9 values (still ~0% RTP either way, per V0.3.9 §6). Net effect:
# size 5-9's RTP share drops from 52.3% to 37.5%, size 10-19 rises from
# 43.9% to 58.6%, verified via both histogram recompute and a direct
# 200,000-book re-simulation (96.72% RTP either way).
CANDIDATE_A_PAYTABLE_STEPS = {
    "L1": {5: 0.10, 6: 0.10, 7: 0.10, 8: 0.10, 9: 0.10, 10: 0.40, 12: 0.50, 15: 0.50, 20: 3.00, 25: 4.00, 30: 15.00},
    "L2": {5: 0.10, 6: 0.10, 7: 0.10, 8: 0.10, 9: 0.10, 10: 0.40, 12: 0.50, 15: 0.50, 20: 3.00, 25: 4.00, 30: 15.00},
    "L3": {5: 0.10, 6: 0.10, 7: 0.10, 8: 0.10, 9: 0.20, 10: 0.50, 12: 0.70, 15: 0.80, 20: 5.00, 25: 7.50, 30: 25.00},
    "L4": {5: 0.10, 6: 0.10, 7: 0.10, 8: 0.10, 9: 0.20, 10: 0.50, 12: 0.70, 15: 0.80, 20: 5.00, 25: 7.50, 30: 25.00},
    "H1": {5: 0.10, 6: 0.20, 7: 0.30, 8: 0.40, 9: 0.60, 10: 1.80, 12: 2.00, 15: 2.40, 20: 15.00, 25: 20.00, 30: 75.00},
    "H2": {5: 0.20, 6: 0.40, 7: 0.60, 8: 0.80, 9: 1.20, 10: 3.40, 12: 3.90, 15: 4.60, 20: 30.00, 25: 40.00, 30: 150.00},
    "H3": {5: 0.40, 6: 0.70, 7: 1.10, 8: 1.30, 9: 1.90, 10: 4.50, 12: 5.10, 15: 5.80, 20: 40.00, 25: 60.00, 30: 250.00},
    "H4": {5: 0.70, 6: 1.40, 7: 2.00, 8: 2.50, 9: 3.70, 10: 8.90, 12: 10.20, 15: 11.40, 20: 80.00, 25: 120.00, 30: 500.00},
}

# V0.3.10 min_cluster_size=6 follow-up (see docs/milestones/
# V0.3.10_n5_rtp_contribution_and_reward_curve_review.md §7): removing the
# size-5 tier drops RTP under the min_cluster_size=5 CANDIDATE_A_PAYTABLE_STEPS
# from 96.72% to 77.36% at the re-derived (16, 32, 48, 64) thresholds (measured
# via a (symbol, size) -> sum(clusterMultiplier) histogram over 200,000 books,
# same recompute-without-resimulating technique as V0.3.9 §5/V0.3.10 §5).
# This is CANDIDATE_A_PAYTABLE_STEPS's Candidate M shape (already reshaped
# away from small clusters) rescaled by a single uniform factor (~1.293,
# then rounded to 0.10 and re-iterated for convergence) to bring RTP back to
# 96.62% -- a pure rescale, not a re-reshape, so the 37.5/58.6/3.9 size-tier
# split from the reward-curve review still applies (size 5's own entry is
# simply never read, since _step_paytable's range starts at
# config.min_cluster_size). The size-5 key is kept anyway so this dict has
# the same shape as CANDIDATE_A_PAYTABLE_STEPS.
CANDIDATE_A_MIN6_PAYTABLE_STEPS = {
    "L1": {5: 0.10, 6: 0.10, 7: 0.10, 8: 0.10, 9: 0.10, 10: 0.50, 12: 0.60, 15: 0.60, 20: 3.90, 25: 5.10, 30: 20.10},
    "L2": {5: 0.10, 6: 0.10, 7: 0.10, 8: 0.10, 9: 0.10, 10: 0.50, 12: 0.60, 15: 0.60, 20: 3.90, 25: 5.10, 30: 20.10},
    "L3": {5: 0.10, 6: 0.10, 7: 0.10, 8: 0.10, 9: 0.30, 10: 0.60, 12: 0.90, 15: 1.00, 20: 6.50, 25: 9.80, 30: 33.10},
    "L4": {5: 0.10, 6: 0.10, 7: 0.10, 8: 0.10, 9: 0.30, 10: 0.60, 12: 0.90, 15: 1.00, 20: 6.50, 25: 9.80, 30: 33.10},
    "H1": {5: 0.10, 6: 0.30, 7: 0.40, 8: 0.50, 9: 0.80, 10: 2.40, 12: 2.60, 15: 3.10, 20: 20.10, 25: 26.60, 30: 99.20},
    "H2": {5: 0.30, 6: 0.50, 7: 0.80, 8: 1.00, 9: 1.50, 10: 4.40, 12: 5.00, 15: 5.90, 20: 39.60, 25: 53.20, 30: 198.60},
    "H3": {5: 0.50, 6: 0.90, 7: 1.40, 8: 1.60, 9: 2.50, 10: 5.70, 12: 6.60, 15: 7.60, 20: 53.20, 25: 79.40, 30: 331.10},
    "H4": {5: 0.90, 6: 1.80, 7: 2.60, 8: 3.20, 9: 4.70, 10: 11.60, 12: 13.30, 15: 15.00, 20: 105.80, 25: 159.00, 30: 662.00},
}

# V0.3.11 7x7 grid follow-up (see docs/milestones/V0.3.11_grid_7x7_study.md):
# num_reels generalized to make a true 7x7 grid possible (previously only
# num_rows was configurable -- V0.3.6 only ever tested 6x6 vs 6x7). That
# milestone's own CANDIDATE_7X7_SYMBOL_WEIGHTS/CANDIDATE_7X7_PAYTABLE_STEPS
# in-house design (32% premium, geometric "regular steps" curve, RTP 97.03%)
# was **superseded in V0.3.13** by a real product-owner paytable brief (see
# docs/milestones/PAYTABLE_7x7_RTP_96_KITCHEN_VS_FLY.md and
# KITCHEN_7X7_RTP96_PAYTABLE_STEPS/KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS below) --
# following this project's own precedent of keeping only the currently
# installed candidate as a code constant (V0.3.9 Sec.12: superseded
# candidates B/C/D live in their milestone report, not in game_config.py).
# The V0.3.11 numbers remain fully recoverable via git history and
# docs/milestones/V0.3.11_grid_7x7_study.md.

# V0.3.13 product-owner-specified 7x7 paytable (see docs/milestones/
# PAYTABLE_7x7_RTP_96_KITCHEN_VS_FLY.md and docs/milestones/
# V0.3.13_paytable_7x7_rtp96.md): a complete, fixed step table supplied
# verbatim by the product brief -- unlike every earlier *_PAYTABLE_STEPS
# table in this file, this one is NOT designed/rescaled to hit an RTP
# target; the brief explicitly requires the paytable to stay unchanged and
# RTP to be reached via symbol_weights/other parameters instead (see
# KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS below). min_cluster_size=5 for this
# pairing (the brief: "un cluster gagnant commence a 5 symboles
# connectes"), so min_size=5 breakpoints exist here unlike the old
# CANDIDATE_7X7_PAYTABLE_STEPS's min_size=6.
#
# Three cells are NOT verbatim: L1/L2 (onion/garlic) at size 5 (brief:
# x0.15) and size 8 (brief: x0.75), and L3/L4 (butter/herbs) at size 9
# (brief: x0.75). None of these are multiples of 0.10x bet, which violates
# this repo's Stake Engine RGS compliance check (verify_lookup_format:
# every non-zero round payout must be a multiple of 0.10x bet -- see
# V0.3.9 Sec.8, where the exact same class of violation was found and
# fixed). Resolved with the product owner (2026-09-20): round these 3
# cells UP to the nearest 0.10 (0.15->0.20, 0.75->0.80 in both places) as
# the only deviation from the brief, explicitly for platform-compliance
# reasons. Every other cell below is the brief's value verbatim.
#
# V0.3.14 UPDATE (see docs/milestones/V0.3.14_kitchen_7x7_rebalance.md):
# these V0.3.13 values are frozen here as KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V013
# (the required historical reference point for the V0.3.14 rebalance's 50%
# floor -- "no cell below 50% of its V0.3.13 value" -- and for
# tests/test_paytable_7x7_rtp96.py's own floor-proof test). The *installed*
# `KITCHEN_7X7_RTP96_PAYTABLE_STEPS` below this block is the V0.3.14
# rebalanced table.
KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V013 = {
    "L1": {5: 0.20, 6: 0.30, 7: 0.50, 8: 0.80, 9: 1.00, 10: 1.50, 12: 2.00, 15: 3.00, 20: 5.00, 25: 7.50, 30: 25.00},
    "L2": {5: 0.20, 6: 0.30, 7: 0.50, 8: 0.80, 9: 1.00, 10: 1.50, 12: 2.00, 15: 3.00, 20: 5.00, 25: 7.50, 30: 25.00},
    "L3": {5: 0.10, 6: 0.20, 7: 0.30, 8: 0.50, 9: 0.80, 10: 1.00, 12: 1.50, 15: 2.00, 20: 3.00, 25: 4.00, 30: 15.00},
    "L4": {5: 0.10, 6: 0.20, 7: 0.30, 8: 0.50, 9: 0.80, 10: 1.00, 12: 1.50, 15: 2.00, 20: 3.00, 25: 4.00, 30: 15.00},
    "H1": {5: 0.50, 6: 1.00, 7: 1.50, 8: 2.00, 9: 3.00, 10: 5.00, 12: 7.50, 15: 10.00, 20: 15.00, 25: 20.00, 30: 75.00},
    "H2": {5: 1.00, 6: 2.00, 7: 3.00, 8: 4.00, 9: 6.00, 10: 10.00, 12: 15.00, 15: 20.00, 20: 30.00, 25: 40.00, 30: 150.00},
    "H3": {5: 1.50, 6: 3.00, 7: 5.00, 8: 7.00, 9: 10.00, 10: 15.00, 12: 20.00, 15: 30.00, 20: 40.00, 25: 60.00, 30: 250.00},
    "H4": {5: 3.00, 6: 6.00, 7: 10.00, 8: 15.00, 9: 20.00, 10: 30.00, 12: 40.00, 15: 60.00, 20: 80.00, 25: 120.00, 30: 500.00},
}

# V0.3.14 rebalanced paytable (see docs/milestones/V0.3.14_kitchen_7x7_rebalance.md):
# the user asked for N2/N4 mechanics restored close to their game-design
# baseline (n2_temp_wild_count, n4_wild_count -- see below) and N5 pulled
# back from V0.3.13's 1-in-299.9 toward the project's usual ~1-in-100-200
# band, while keeping RTP ~=96%. Restoring those mechanics alone (at
# V0.3.13's own weights/thresholds/paytable) measured RTP 409.7% (Phase A);
# re-searching progression_thresholds alone (Phase B) found a region with
# N2~1-in-18 and N5~1-in-195-220 at a *full-paytable* RTP of ~173-208%
# (thresholds (10,20,50,62), n2_temp_wild_count=2 -- a partial, not full,
# N2 restore was necessary; n4_wild_count=3 is the *full* documented N4
# baseline) -- still roughly 2x over target, confirming the user's own
# expectation that paytable reduction would be needed (Phase C). An
# RTP-by-tier histogram at that config showed premiums alone are 86% of
# RTP and cutting them to the 50% floor alone still left RTP at ~108% (a
# targeted, premiums-only cut is NOT sufficient); low symbols needed
# cutting too. A near-uniform ~53% scale of every cell (rounded to 0.10,
# each cell individually re-checked against its own 50% floor) converged
# at RTP 94.8%/95.5% (two independent 100,000-book direct re-simulations)
# -- inside Monte Carlo noise of 96.00%, 0 wincap hits observed in either
# run (vs. V0.3.13's occasional hits at this same paytable family). Every
# cell here is a verbatim `round(KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V013 *
# ~0.53, nearest 0.10)`, with any cell that would have rounded below its
# 50% floor bumped up to the nearest 0.10 multiple that still clears it
# (`tests/test_paytable_7x7_rtp96.py::test_7x7_rtp96_no_cell_below_v013_floor`
# proves this for all 88 cells) -- relative order (H4>H3>H2>H1,
# L1=L2>L3=L4) and per-symbol monotonicity by size are both unchanged from
# V0.3.13, since a positive uniform-ish scale preserves both.
#
# V0.3.16 UPDATE (see docs/milestones/V0.3.16_kitchen_7x7_paytable_rebuild.md):
# frozen here as KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V014, the reference point
# for that milestone's own analysis. Superseded because this table has
# L1=L2 paying >= L3=L4 at every breakpoint (e.g. size 30: L1/L2 x13.20 vs
# L3/L4 x8.00) while V0.3.15 locked symbol_weights making L1/L2 (19% each)
# MORE common than L3/L4 (15% each) -- the product owner's game design
# requires the opposite: L1=L2 < L3=L4 in value, since Beurre/Herbes are
# meant to sit economically between Oignon/Ail and Couteau, not below them.
KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V014 = {
    "L1": {5: 0.10, 6: 0.20, 7: 0.30, 8: 0.40, 9: 0.50, 10: 0.80, 12: 1.10, 15: 1.60, 20: 2.70, 25: 4.00, 30: 13.20},
    "L2": {5: 0.10, 6: 0.20, 7: 0.30, 8: 0.40, 9: 0.50, 10: 0.80, 12: 1.10, 15: 1.60, 20: 2.70, 25: 4.00, 30: 13.20},
    "L3": {5: 0.10, 6: 0.10, 7: 0.20, 8: 0.30, 9: 0.40, 10: 0.50, 12: 0.80, 15: 1.10, 20: 1.60, 25: 2.10, 30: 8.00},
    "L4": {5: 0.10, 6: 0.10, 7: 0.20, 8: 0.30, 9: 0.40, 10: 0.50, 12: 0.80, 15: 1.10, 20: 1.60, 25: 2.10, 30: 8.00},
    "H1": {5: 0.30, 6: 0.50, 7: 0.80, 8: 1.10, 9: 1.60, 10: 2.70, 12: 4.00, 15: 5.30, 20: 8.00, 25: 10.60, 30: 39.80},
    "H2": {5: 0.50, 6: 1.10, 7: 1.60, 8: 2.10, 9: 3.20, 10: 5.30, 12: 8.00, 15: 10.60, 20: 15.90, 25: 21.20, 30: 79.50},
    "H3": {5: 0.80, 6: 1.60, 7: 2.70, 8: 3.70, 9: 5.30, 10: 8.00, 12: 10.60, 15: 15.90, 20: 21.20, 25: 31.80, 30: 132.50},
    "H4": {5: 1.60, 6: 3.20, 7: 5.30, 8: 8.00, 9: 10.60, 10: 15.90, 12: 21.20, 15: 31.80, 20: 42.40, 25: 63.60, 30: 265.00},
}

# V0.3.16 rebuilt paytable (see docs/milestones/V0.3.16_kitchen_7x7_paytable_rebuild.md):
# built from V0.3.15's locked pairs_A weights/thresholds, this table exists
# to fix the L1/L2-vs-L3/L4 ordering above. Method: measured (via a
# 500,000-book reference sample's raw (symbol, cluster-size, N4-multiplier)
# histogram, cross-checked to within 0.003 RTP points against that same
# sample's real observed RTP) that simply SWAPPING which curve L1/L2 vs
# L3/L4 use -- L1/L2 (more common, 19% each) take V0.3.14's old L3/L4
# curve, L3/L4 (rarer, 15% each) take V0.3.14's old L1/L2 curve, H1-H4
# entirely untouched -- fixes the entire hierarchy with only ONE
# unavoidable tie (both L1/L2 and L3/L4 pinned at the 0.10 RGS floor at
# size 5, same kind of exception V0.3.14 itself documented) while landing
# close to the RTP target as a side effect of the reassignment itself, not
# a separate cut. That swap alone measured RTP 97.81% on a real 3,000,000-
# book validation (batches independently seeded, 0 wincap hits) -- close
# but above the 95.8-96.2% preferred band, and notably different from the
# 95.96% the 500,000-book reference histogram had predicted for it: the
# gap is real Monte Carlo noise in that one reference draw, not a flaw in
# the histogram technique itself (N5 alone drives ~85% of RTP from under
# 1% of rounds, the same heavy-tailed-at-large-samples behaviour V0.3.9
# Sec.11 and V0.3.14 Sec.4.4 already documented) -- confirms a paytable's
# own histogram estimate is only as converged as its reference sample, not
# an exact oracle. A uniform x0.9815 rescale of the swap table (= 96.00 /
# 97.807, the actual 3,000,000-book measurement, each cell re-rounded to
# 0.10) was then itself validated on a SECOND independent 3,000,000-book
# run: RTP 96.567% (batch range 93.80-102.48%, 1 wincap hit / 3,000,000).
# Still only the one unavoidable size-5 tie; H1-H4 shifted by at most a
# few tenths of a percent from V0.3.14's own values (e.g. H4 size 30:
# 265.00 -> 260.10), nowhere near disproportionate. This table's cells are
# NOT individually checked against the V0.3.13 50% floor
# (KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V013) -- that constraint was specific
# to the V0.3.14 rebalance exercise (preserving V0.3.13's own paytable
# identity), not a standing rule, and is structurally incompatible with
# swapping which symbol occupies which value curve (20/88 cells here,
# every L1/L2 breakpoint from size 10 up, fall below it by construction).
KITCHEN_7X7_RTP96_PAYTABLE_STEPS = {
    "L1": {5: 0.10, 6: 0.10, 7: 0.20, 8: 0.30, 9: 0.40, 10: 0.50, 12: 0.80, 15: 1.10, 20: 1.60, 25: 2.10, 30: 7.90},
    "L2": {5: 0.10, 6: 0.10, 7: 0.20, 8: 0.30, 9: 0.40, 10: 0.50, 12: 0.80, 15: 1.10, 20: 1.60, 25: 2.10, 30: 7.90},
    "L3": {5: 0.10, 6: 0.20, 7: 0.30, 8: 0.40, 9: 0.50, 10: 0.80, 12: 1.10, 15: 1.60, 20: 2.70, 25: 3.90, 30: 13.00},
    "L4": {5: 0.10, 6: 0.20, 7: 0.30, 8: 0.40, 9: 0.50, 10: 0.80, 12: 1.10, 15: 1.60, 20: 2.70, 25: 3.90, 30: 13.00},
    "H1": {5: 0.30, 6: 0.50, 7: 0.80, 8: 1.10, 9: 1.60, 10: 2.70, 12: 3.90, 15: 5.20, 20: 7.90, 25: 10.40, 30: 39.00},
    "H2": {5: 0.50, 6: 1.10, 7: 1.60, 8: 2.10, 9: 3.10, 10: 5.20, 12: 7.90, 15: 10.40, 20: 15.60, 25: 20.80, 30: 78.00},
    "H3": {5: 0.80, 6: 1.60, 7: 2.70, 8: 3.60, 9: 5.20, 10: 7.90, 12: 10.40, 15: 15.60, 20: 20.80, 25: 31.20, 30: 130.00},
    "H4": {5: 1.60, 6: 3.10, 7: 5.20, 8: 7.90, 9: 10.40, 10: 15.60, 12: 20.80, 15: 31.20, 20: 41.60, 25: 62.40, 30: 260.00},
}
# V0.3.18 cosmetic rounding (see docs/milestones/V0.3.18_kitchen_7x7_fly_threshold_screening.md):
# the three top-tier (size 30+) cells that landed one cent away from a round
# number after V0.3.16's x0.9815 rescale -- H1 39.10->39.00, H3 130.10->130.00,
# H4 260.10->260.00 -- were rounded DOWN to the clean integer. Deliberately
# narrow: only cells ending in exactly ".10" at a "large" (>=10) value were
# touched (the residual is <=0.26% of the cell's own value in every case);
# H1[25]=10.40, H4[25]=62.40 and every other ".x0"-but-not-".10" cell was left
# alone, since those decimals are a real part of the rescale, not rounding
# noise. Value-hierarchy (L1=L2 < L3=L4 < H1 < H2 < H3 < H4) and per-symbol
# monotonicity by size are unaffected (each touched cell only shrank by
# 0.10, staying above its own size-25 value and below the next symbol's
# size-30 value).

# V0.3.13 calibrated weights for the fixed paytable above (see
# docs/milestones/V0.3.13_paytable_7x7_rtp96.md for the full calibration
# log). Unlike every earlier weight set in this file, these were NOT
# anchored on a fixed ~30-35% premium-share target: with this specific
# paytable's premium values so much richer than its low values (H4 pays up
# to x500 vs L1's x25 -- a ~20x gap at every matching breakpoint), holding
# the old candidates' internal premium skew (H1 noticeably more common than
# H4) at ANY substantial aggregate premium weight reliably produced runaway
# RTP (measured: premium fixed at the old 32% split with only a mild low-tier
# dilution still gave 117-448% RTP). The safe region found instead is much
# closer to flat across all 8 symbols (a small skew, not a strong one): low
# tier keeps L1=L2 > L3=L4 and premium tier keeps H1>H2>H3>H4, but the gaps
# are shallow enough that premium share ends up at 42.9% -- confirmed via
# iterative direct re-simulation (no free histogram-rescale shortcut is
# available for a weight search, unlike a paytable rescale: changing weights
# changes the reel strips themselves). Converged at RTP 96.75%/96.93%/93.93%
# across three independent 100,000-200,000-book calibration runs, confirmed
# at RTP 95.71% on the final 200,000-book verification pass (replay-validated,
# 0 errors) -- averaging ~95.8% across all four, inside Monte Carlo noise of
# the 96.00% target (V0.3.9 Sec.11 precedent).
#
# V0.3.14 UPDATE: re-used byte-identical. The V0.3.14 search (see
# docs/milestones/V0.3.14_kitchen_7x7_rebalance.md) tried diluting these
# weights further (toward equal) while mechanics were fully restored, but
# found dilution pushed N2 access far outside convention (down to
# 1-in-40+) without meaningfully lowering RTP (which stayed 216-247% across
# the whole 0.0-0.30 skew-interpolation range once N2/N4 mechanics were
# both fully restored) -- RTP here is dominated by feature-access frequency
# via progression_thresholds and paytable magnitude, not by weight
# concentration, once mechanics are strong. The eventual solution instead
# used these exact V0.3.13 weights unchanged, re-derived thresholds (below)
# and a rebalanced paytable (above).
#
# V0.3.16 UPDATE (see docs/milestones/V0.3.15_kitchen_7x7_pairs_weights_and_thresholds.md
# and docs/milestones/V0.3.16_kitchen_7x7_paytable_rebuild.md): frozen here
# as KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS_V014. Superseded in V0.3.15 because
# L3=L4 (10 each, 10.2%) were as rare as or rarer than every premium
# (H1=12 at 12.2%, H2=11 at 11.2%) despite needing to pay LESS than every
# premium while appearing MORE often -- a direct violation of "rarer must
# pay more" once combined with the paytable above.
KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS_V014 = {
    "L1": 18, "L2": 18, "L3": 10, "L4": 10,
    "H1": 12, "H2": 11, "H3": 10, "H4": 9,
}

# V0.3.15 "pairs_A" weights (see docs/milestones/
# V0.3.15_kitchen_7x7_pairs_weights_and_thresholds.md): chosen by the
# product owner from 7 tested candidates (screening 560,000 books +
# intermediate validation 900,000 books) to restore the intended frequency
# hierarchy Oignon=Ail > Beurre=Herbes > Couteau > Plat > Gants > Poulet
# roti, i.e. L1=L2 > L3=L4 > H1 > H2 > H3 > H4. Ties L1=L2 (both 19, 19%)
# and L3=L4 (both 15, 15%) exactly, low share 68%/premium share 32% (up
# from V0.3.14's 57.1%/42.9%). Preferred over "indiv_A" (20/18/16/14/12/
# 9/6/5), a measured but modest improvement (closer to the N5 100-150
# ideal band, more stable across samples) that breaks the L1=L2/L3=L4
# symmetry the product owner's game design calls for -- per their own
# tie-breaking rule ("if close, prefer pairs").
KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS = {
    "L1": 19, "L2": 19, "L3": 15, "L4": 15,
    "H1": 11, "H2": 9, "H3": 7, "H4": 5,
}


def _step_paytable(symbols, steps, max_size, min_size=5):
    """Expand a {symbol: {breakpoint: value}} step table (see
    CANDIDATE_A_PAYTABLE_STEPS) into a full {(size, symbol): value} paytable
    for sizes min_size..max_size. Every size pays the value of the highest
    breakpoint it has reached; sizes at or above the highest breakpoint
    (30) all pay that same value — the product owner's explicit "size 30
    and up are all worth the same" rule. `min_size` (V0.3.10) matters
    because src.calculations.cluster only pays a cluster whose (size,
    symbol) key exists in the paytable at all -- there is no separate
    "minimum cluster size" enforcement anywhere else in the engine, so
    starting this range at config.min_cluster_size rather than a hardcoded
    5 is what actually raises the floor when `min_cluster_size=6` is
    passed to GameConfig."""
    table = {}
    for symbol in symbols:
        tiers = steps[symbol]
        breakpoints = sorted(tiers)
        for size in range(min_size, max_size + 1):
            bp = breakpoints[0]
            for candidate in breakpoints:
                if size >= candidate:
                    bp = candidate
                else:
                    break
            table[size, symbol] = tiers[bp]
    return table


def _reel_strip(symbols, weights, length=800):
    """One column's cyclic strip before its per-column shuffle. `weights`
    is None for the legacy equal-weight behaviour (100 copies of each of
    the 8 symbols — byte-identical to every pre-V0.3.8 config); otherwise a
    {symbol: weight} dict (weights need not sum to 1) is converted to
    integer copy counts via largest-remainder rounding so the strip still
    has exactly `length` entries however the weights divide it."""
    if weights is None:
        return list(symbols) * (length // len(symbols))
    total_weight = sum(weights[s] for s in symbols)
    raw_counts = [weights[s] * length / total_weight for s in symbols]
    counts = [int(v) for v in raw_counts]
    remainder = length - sum(counts)
    fractional_order = sorted(range(len(symbols)), key=lambda i: raw_counts[i] - counts[i], reverse=True)
    for i in fractional_order[:remainder]:
        counts[i] += 1
    strip = []
    for symbol, count in zip(symbols, counts):
        strip.extend([symbol] * count)
    return strip


class GameConfig(Config):
    def __init__(self, num_rows=6, progression_thresholds=None, symbol_weights=None,
                 provisional_paytable=False, production=False, min_cluster_size=None,
                 num_reels=6, symbols=None, wincap=None, golden_ladder=None,
                 golden_trigger_probability=0.0, golden_books=0, wincap_books=0,
                 bonus_buy_books=0, bonus_buy_criteria="buy_n5"):
        """`symbols` (V0.3.12 follow-up, exploratory symbol-count study) lets
        a caller drop one or more of the 8 standard symbols entirely (e.g.
        removing a low symbol so the remaining lows share the same 68%
        low-tier weight total among fewer symbols, raising each one's
        individual board frequency and therefore hit rate). Defaults to the
        standard 8-symbol tuple (byte-identical to every caller that omits
        it). `symbol_weights`/`premium_symbols`/the paytable/reel strips all
        already iterate over `self.symbols` generically (never a hardcoded
        "4 lows + 4 premiums" assumption), so dropping a symbol here is the
        only change needed -- the passed `symbol_weights` dict simply omits
        (or ignores, if present) the dropped symbol's key.

        `num_rows` and `progression_thresholds` are the minimum safe
        generalization added for the V0.3.6 6x6-vs-6x7 grid study (see
        docs/milestones/V0.3.6_grid_6x6_vs_6x7_study.md): every caller that
        omits them (all production code and all pre-V0.3.6 tests) gets back
        byte-identical 6x6/DEV-threshold behaviour. `num_rows` is uniform
        across all six reels — the engine (src/calculations/board.py,
        src/calculations/tumble.py) supports a per-reel list, but nothing in
        Kitchen vs Fly ever needs a non-uniform grid, so only a single int is
        exposed here.

        `num_reels` (V0.3.11 follow-up, see docs/milestones/
        V0.3.11_grid_7x7_study.md) generalizes the reel *count* the same way
        `num_rows` already generalizes row count -- V0.3.6 only ever varied
        num_rows (6x6 vs 6x7), never num_reels, so a true 7x7 grid needs
        this. Defaults to 6 (byte-identical to every pre-V0.3.11 caller).
        The rest of the engine already reads `config.num_reels` generically
        wherever grid width matters (N4/N5 corner-placement math in
        gamestate.py, reel-strip construction, the paytable's max-cluster-size
        bound) -- no other hardcoded reel-count assumption was found, so this
        parameter is the only change needed to unlock non-6-reel grids.

        `symbol_weights` and `provisional_paytable` are the V0.3.8
        symbol-frequency/paytable generalization (see docs/milestones/
        V0.3.8_symbol_frequencies_fly_minibonus_20_40_60_80.md): both
        default to the pre-V0.3.8 behaviour (equal-weight reels, DEV
        rank*(size-4)/10 paytable) so no caller that omits them is
        affected. `symbol_weights` is an optional {symbol: weight} dict
        (any positive numbers, not required to sum to 1); passing it is the
        ONLY supported way to make regular symbols non-uniform — the reel
        strips remain the sole source of symbol probability, consistent
        with how refill already works (see tumble_board in gamestate.py).

        `production` (V0.3.9, see docs/milestones/
        V0.3.9_candidate_a_calibration.md) installs the calibrated
        "Candidate A" configuration chosen by the product owner:
        `progression_thresholds` default to (20, 40, 60, 80),
        `symbol_weights` default to `CANDIDATE_A_SYMBOL_WEIGHTS`, N2 gets 3
        additional temporary Wilds (`n2_temp_wild_count=3`), and the
        paytable becomes `CANDIDATE_A_PAYTABLE_STEPS` (overrides
        `provisional_paytable`). Explicitly passing `progression_thresholds`
        or `symbol_weights` still overrides the Candidate A default for
        that argument. Default `False`, so every existing caller (all
        pre-V0.3.9 tests, and any caller that omits it) is unaffected.

        `min_cluster_size` (V0.3.10 follow-up study, see docs/milestones/
        V0.3.10_n5_rtp_contribution_and_reward_curve_review.md) is an
        explicit opt-in override, deliberately NOT defaulted by
        `production=True` the way `progression_thresholds`/`symbol_weights`
        are: raising it changes how many winning positions `gain()`
        accumulates per round (fewer, bigger clusters), which shifts the
        already-calibrated N2-N5 access rates (measured: N2 access
        1-in-13.2 -> 1-in-28.1, N5 1-in-121.4 -> 1-in-1075.3 at the old
        20/40/60/80 thresholds, RTP 96.72% -> 20.37%, everything else
        unchanged) -- raising the floor alone, with no other change, is
        NOT production-ready.
        `production=True, min_cluster_size=6` together install the
        re-derived pairing found for that regime: `progression_thresholds`
        default to `(16, 32, 48, 64)` (re-derived via bisection search to
        restore N2/N5 access close to the min_cluster_size=5 targets: 1-in-14.0
        and 1-in-126.8 measured over 200,000 books) and the paytable becomes
        `CANDIDATE_A_MIN6_PAYTABLE_STEPS` (Candidate M's reshaped curve,
        uniformly rescaled from the histogram-recompute technique to bring
        RTP from 77.36% back to 96.62%). Explicitly passing
        `progression_thresholds` still overrides this pairing's default, same
        precedence as every other production default. `min_cluster_size`
        itself always defaults to 5 (byte-identical to every pre-V0.3.10
        caller) whether or not `production` is set.

        `production=True, num_reels=7` installs the 7x7 pairing, **replaced
        in V0.3.13** (see docs/milestones/PAYTABLE_7x7_RTP_96_KITCHEN_VS_FLY.md
        and docs/milestones/V0.3.13_paytable_7x7_rtp96.md) by a real
        product-owner paytable brief -- the V0.3.11 in-house "regular steps"
        design is superseded, not merely retuned -- **rebalanced in V0.3.14**
        (see docs/milestones/V0.3.14_kitchen_7x7_rebalance.md) to restore
        V0.3.13's two RTP-reduction levers closer to their game-design
        baseline and pull N5 access back from V0.3.13's 1-in-299.9 into a
        more usual band, and **rebuilt again in V0.3.15/V0.3.16** (see
        docs/milestones/V0.3.15_kitchen_7x7_pairs_weights_and_thresholds.md
        and docs/milestones/V0.3.16_kitchen_7x7_paytable_rebuild.md) to fix
        a symbol-hierarchy inconsistency V0.3.14 left in place: Beurre/Herbes
        (L3/L4) were as rare as or rarer than every premium while paying
        less than all of them. `min_cluster_size` defaults to 5 (the brief:
        a winning cluster starts at 5 connected symbols), `symbol_weights`
        default to `KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS` (V0.3.15's "pairs_A":
        `L1=L2=19, L3=L4=15, H1=11, H2=9, H3=7, H4=5` -- restores the
        intended Oignon=Ail > Beurre=Herbes > H1 > H2 > H3 > H4 frequency
        order; V0.3.13/V0.3.14's shallower weights are frozen as
        `KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS_V014`), `progression_thresholds`
        default to `(10, 26, 40, 55)` (V0.3.19, installed 2026-09-25 together
        with the Fly mini-bonus: enabled, 0.057143, `fly_wild_counts=(2, 3)`;
        V0.3.15-V0.3.18 used `(10, 25, 40, 55)` with Fly disabled, and
        V0.3.14 `(10, 20, 50, 62)`), the
        paytable becomes `KITCHEN_7X7_RTP96_PAYTABLE_STEPS` (V0.3.16: L1/L2
        and L3/L4 swap which curve they use relative to V0.3.14, fixing
        `L1=L2 < L3=L4 < H1 < H2 < H3 < H4` at every breakpoint but one
        unavoidable RGS-floor tie, H1-H4 essentially untouched, then a
        uniform ~0.98x rescale to reach RTP target -- see that constant's
        own comment; V0.3.14's table is frozen as
        `KITCHEN_7X7_RTP96_PAYTABLE_STEPS_V014`), `n2_temp_wild_count`
        defaults to 2 (V0.3.13: 0; the documented production baseline is 3
        -- a deliberate partial, not full, restore) and `n4_wild_count`
        defaults to 3 (V0.3.13: 1; V0.3.14 restores the full documented
        baseline; both unchanged since V0.3.14). The declared RTP is 0.965.
        Measured (V0.3.19, large-scale final validation, replay-validated,
        3,000,000 books): RTP 96.501%, N5 1-in-117.6, Fly 1-in-17.54 -- see
        docs/milestones/V0.3.19_kitchen_7x7_fly_10_26_40_55_validation.md.
        This 7x7 pairing is the Kitchen vs Fly production configuration used
        by the frontend; the 6x6 Candidate A pairing (bare
        `production=True`) is kept for history and tests only. Each of these defaults can still be overridden
        explicitly, same precedence as every other
        production default. `num_reels` itself always defaults to 6
        (byte-identical to every pre-V0.3.11 caller) whether or not
        `production` is set; passing `num_reels=7` without `production=True`
        has no special defaults."""
        super().__init__()
        is_7x7 = num_reels == 7
        if production:
            if min_cluster_size is None and is_7x7:
                min_cluster_size = 5
            if progression_thresholds is None:
                if is_7x7:
                    # V0.3.14: (10, 20, 30, 50) in V0.3.13 -> (10, 20, 50, 62)
                    # here, N2/N3 thresholds unchanged, N4/N5 pushed further
                    # out to bring N5 access back from V0.3.13's 1-in-299.9
                    # into the project's usual band while mechanics are
                    # restored -- see docs/milestones/V0.3.14_kitchen_7x7_rebalance.md.
                    # V0.3.15 update (see docs/milestones/
                    # V0.3.15_kitchen_7x7_pairs_weights_and_thresholds.md):
                    # re-derived for the pairs_A weights above -- t2 pushed
                    # 20->25 and t3/t4 pulled in (50->40, 62->55), a regular
                    # +15 spacing after t1, chosen over an equally-tested
                    # (10,20,40,60) for its much lower inter-sample variance
                    # (screening stdev 0.13 vs 1.70) and stronger N5
                    # concentration of RTP (85.5% vs 71.7%), both landing
                    # N5 inside the 1-in-100-150 ideal band unlike V0.3.14's
                    # own 1-in-196.
                    # V0.3.19 (installed 2026-09-25, see docs/milestones/
                    # V0.3.19_kitchen_7x7_fly_10_26_40_55_validation.md):
                    # (10, 25, 40, 55) -> (10, 26, 40, 55), N3 gate +1 only,
                    # paired with the Fly mini-bonus enabled below. Selected by
                    # V0.3.18's screening, validated on 3,000,000 books
                    # (RTP 96.501%, N5 1-in-117.6). (10, 25, 40, 55) with
                    # Fly measured RTP 104.911% (V0.3.17).
                    progression_thresholds = (10, 26, 40, 55)
                elif min_cluster_size == 6:
                    progression_thresholds = (16, 32, 48, 64)
                else:
                    progression_thresholds = (20, 40, 60, 80)
            if symbol_weights is None:
                symbol_weights = KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS if is_7x7 else CANDIDATE_A_SYMBOL_WEIGHTS
        self.game_id = "kitchen_vs_fly"
        self.game_name = self.game_id
        self.working_name = "Kitchen vs Fly"
        self.construct_paths()
        self.num_reels = num_reels
        self.num_rows = [num_rows] * self.num_reels
        self.win_type = "cluster"
        self.include_padding = False
        # "W" (N2, persistent), "WN2T" (N2, temporary — opt-in "mixed Wilds"
        # variant under calibration, see n2_temp_wild_count below), "WN4" (N4,
        # temporary), "WN5" (N5 Mega Wild footprint cells) and "WFLY" (V0.3.8
        # pre-N1 Fly mini-bonus, temporary) are all Wilds for cluster
        # matching, but only "W" is sticky (fixed, survives removal). Keeping
        # them as distinct names is what lets the event model and the replay
        # validator tell the five lifecycles apart (see GAME_DESIGN.md). WN5
        # is not "sticky" either: its footprint is protected from
        # explode/gravity by dedicated N5 logic in gamestate.py
        # (self.n5_boss_positions), not by the generic sticky_positions
        # mechanism, because unlike a sticky Wild it must sometimes be
        # removed (when the boss shrinks/moves/dies). WN2T/WFLY need no
        # dedicated protection at all: each is an ordinary, non-sticky board
        # symbol, so the engine's normal explode/gravity already implements
        # "removed the moment it wins, persists otherwise" with zero extra
        # state (see activate_n2_wilds/activate_fly_minibonus in gamestate.py).
        self.special_symbols = {"wild": ["W", "WN2T", "WN4", "WN5", "WFLY"], "sticky": ["W"]}
        # Technical codes follow the Stake Engine sample convention (see
        # games/0_0_cluster: L1-L4 low, H1-H4 high) so ranks/paytable stay
        # comparable and the legacy fantasy tests keep working unmodified.
        # H1/H2 keep the ranks they already had under the six-symbol dev
        # config; H3/H4 are new. Kitchen vs Fly narrative names below are
        # DEV/PROVISIONAL: not final art direction, only a label mapping.
        self.symbols = symbols if symbols is not None else ("L1", "L2", "L3", "L4", "H1", "H2", "H3", "H4")
        # DEV/PROVISIONAL Kitchen vs Fly mapping. Realigned in V0.3.8 so the
        # narrative role each code plays matches PROVISIONAL_PAYTABLE_5_6_7's
        # tiers exactly (Onion/Garlic cheapest lows, Butter/Herbs the next
        # tier, Oven Gloves renamed from "oven_mitt" to match GAME_DESIGN.md's
        # own naming) — a display-label change only, not a technical rename:
        # L1-L4/H1-H4 remain the stable identifiers (see GAME_DESIGN.md).
        self.symbol_display_names = {
            "L1": "onion", "L2": "garlic", "L3": "butter", "L4": "herbs",
            "H1": "chef_knife", "H2": "roasting_dish", "H3": "oven_gloves", "H4": "roast_chicken",
        }
        # Explicit symbol classification for N3 (see below): the four premium
        # types eligible for conversion. Low symbols are never touched by N3.
        self.premium_symbols = ("H1", "H2", "H3", "H4")
        self.min_cluster_size = min_cluster_size if min_cluster_size is not None else 5
        # Development thresholds by default (see DEVELOPMENT.md); the V0.3.6
        # grid study additionally exercises the candidate production-like
        # 25/50/75/100 thresholds via progression_thresholds, still neither
        # validated nor final (see docs/milestones/V0.3.6_grid_6x6_vs_6x7_study.md).
        self.progression_rules = (
            ProgressionRules(thresholds=tuple(progression_thresholds))
            if progression_thresholds is not None else ProgressionRules()
        )
        # N2 — roast chicken protection: persistent Wilds. Generalizes the old
        # Armor sticky-Wild mechanism (see DEVELOPMENT.md) at its correct
        # Kitchen vs Fly level (N2 / level 2, not the old level 3). Wild count
        # 2 vs 3 is a DEV/PROVISIONAL choice (see GAME_DESIGN.md); kept
        # configurable on purpose. Default set to 2 after the V0.3.2
        # comparison simulation: 3 Wilds showed a much heavier tail (10k
        # books: RTP 0.117 vs 0.280, max win 8.2x vs 246x, max cascades 7 vs
        # 135) driven by persistent Wilds sustaining long cascade chains.
        # This is a provisional lean, not a final balancing decision — see
        # docs/milestones/V0.3.2_kitchen_n2_persistent_wilds.md.
        self.n2_wild_enabled = True
        self.n2_wild_count = 2
        # Optional "mixed Wilds" variant under calibration (see
        # docs/milestones/ for the N2-N5 access-rate study once written up):
        # in addition to the n2_wild_count sticky Wilds above, place this
        # many further temporary "WN2T" Wilds at the same N2 activation —
        # ordinary, non-sticky board symbols (see the special_symbols
        # comment above), meant to boost the round's continuation past N2
        # without permanently sustaining every later cascade the way an
        # equivalent increase to n2_wild_count itself would. Default 0 →
        # byte-identical to every caller that omits it. `production=True`
        # installs the calibrated Candidate A value (3); the 7x7 RTP96
        # pairing installed 0 in V0.3.13 (that paytable left no RTP budget
        # for extra continuation Wilds), then 2 in V0.3.14 -- a deliberate,
        # documented partial (not full) restore: the user asked for
        # mechanics to be preserved close to their game-design baseline
        # while still reaching RTP~=96% with N5 back in a reasonable access
        # band, and a full restore to 3 measured RTP still far too high even
        # combined with re-derived thresholds and the maximum allowed 50%
        # paytable cut (see docs/milestones/V0.3.14_kitchen_7x7_rebalance.md).
        self.n2_temp_wild_count = (2 if is_7x7 else 3) if production else 0
        # N3 — active fly hunting: premium symbol in-place conversion. Once per
        # round at level 3, inspects the premium types (H1-H4) present on the
        # board, picks one of them as the target, and converts every other
        # premium type into it in place: no removal, no gravity, no refill.
        # Low symbols and N2 persistent Wilds are never touched (see GAME_DESIGN.md,
        # updated 2026-09-17: this replaces the earlier remove/refill description).
        # Target selection is a uniform random draw among premium types
        # currently present — the simplest deterministic baseline explicitly
        # marked DEV/PROVISIONAL in design/FEATURE_IDEAS.md; weighted or
        # potential-based selection remain open alternatives, not implemented here.
        self.n3_conversion_enabled = True
        # N4 — determination: evolving 3x3 multiplier zone + temporary Wilds.
        # Once per round at level 4: a contiguous 3x3 block of grid POSITIONS
        # (not symbols) immediately becomes x2 multiplier cells, three
        # distinct positions inside that zone get a temporary "WN4" Wild
        # (never sticky, unlike N2's "W"), and the board is reevaluated
        # immediately (same no-removal/no-gravity pattern as N3). A
        # multiplier position steps up by one (capped at n4_multiplier_cap)
        # each time it takes part in a paid win; several multiplier
        # positions in the same win ADD together, never multiply (V1 rule,
        # see GAME_DESIGN.md). Zone placement (uniform among every valid 3x3
        # top-left corner) and Wild-count (3) are the current DEV/PROVISIONAL
        # baseline from design/FEATURE_IDEAS.md, not a final balancing
        # decision; both are isolated in gamestate.py so either can be
        # swapped without touching the rest of the mechanic.
        self.n4_multiplier_enabled = True
        self.n4_zone_size = 3
        # V0.3.13 temporarily installed 1 temporary Wild for the 7x7 RTP96
        # pairing instead of the documented baseline of 3, purely as an
        # RTP-reduction lever. V0.3.14 restored the full baseline (3) for
        # every pairing, 7x7 included, per the user's explicit request to
        # keep N4 at its game-design baseline (see
        # docs/milestones/V0.3.14_kitchen_7x7_rebalance.md) -- RTP is instead
        # controlled via progression_thresholds, a partial n2_temp_wild_count
        # restore, and a floor-respecting paytable rebalance.
        self.n4_wild_count = 3
        self.n4_multiplier_initial = 2
        self.n4_multiplier_cap = 5
        # N5 — boss: Mega Wild fly, one multi-cell entity shrinking
        # 3x3 -> 2x2 -> 1x1 -> defeated. Once per round at level 5: the
        # initial 3x3 footprint avoids both the N4 zone and any N2 Wild
        # position (see select_n5_initial_top_left in gamestate.py for the
        # documented fallback when no N4-free placement exists); the 2x2/1x1
        # forms may overlap N4 but never an N2 Wild. n5_boss_size is the
        # *initial* size; the state machine shrinks 3->2->1->0 (defeated).
        # See docs/milestones/V0.3.5_kitchen_n5_mega_wild_fly.md.
        self.n5_boss_enabled = True
        self.n5_boss_size = 3
        # All historical fantasy features (Armor, Sword, Boots, the Dragon
        # boss) have been removed from this repo — their mechanisms are now
        # generalized by N2/N4/N5 themselves. Armor -> N2 (V0.3.2), the Dragon
        # boss -> N5 (V0.3.5); Boots and Sword had no active-by-default
        # equivalent and were removed outright during the Kitchen vs Fly
        # cleanup pass rather than kept disabled. See
        # docs/milestones/V0.3.2_kitchen_n2_persistent_wilds.md,
        # docs/milestones/V0.3.4_kitchen_n4_multiplier_zone.md and
        # docs/milestones/V0.3.5_kitchen_n5_mega_wild_fly.md for the removal
        # rationale of each.
        # Tenths of the bet: rank * (size - 4) / 10. Not the sample paytable.
        # Upper bound is the true maximum possible cluster size (every cell on
        # the board): 37 for the original 6x6 grid (range stop is exclusive,
        # 6*6+1), but a hardcoded 37 would silently omit sizes 37-42 on a 6x7
        # grid (found during the V0.3.6 grid study) — src.calculations.cluster
        # skips any (size, symbol) pair missing from this dict rather than
        # raising, so a full-board cluster would silently pay 0 instead of
        # erroring. Deriving the bound from num_reels/num_rows fixes this for
        # any grid size while leaving the 6x6 table byte-identical.
        if production:
            if is_7x7:
                steps = KITCHEN_7X7_RTP96_PAYTABLE_STEPS
            elif self.min_cluster_size == 6:
                steps = CANDIDATE_A_MIN6_PAYTABLE_STEPS
            else:
                steps = CANDIDATE_A_PAYTABLE_STEPS
            self.paytable = _step_paytable(
                self.symbols, steps, self.num_reels * num_rows,
                min_size=self.min_cluster_size,
            )
        elif provisional_paytable:
            self.paytable = _provisional_paytable(self.symbols, self.num_reels * num_rows)
        else:
            self.paytable = {
                (size, symbol): rank * (size - 4) / 10
                for rank, symbol in enumerate(self.symbols, start=1)
                for size in range(self.min_cluster_size, self.num_reels * num_rows + 1)
            }
        # Each column has its own reproducible shuffle. `symbol_weights=None`
        # (default) is 100 copies per symbol — marginal frequency = 1/8,
        # byte-identical to every pre-V0.3.8 config; neighbouring cells are
        # always reelstrip-correlated either way. A DEV/PROVISIONAL
        # placeholder either way: equal weighting is not a validated symbol
        # distribution any more than a supplied non-uniform one is (see
        # docs/milestones/V0.3.8_symbol_frequencies_fly_minibonus_20_40_60_80.md).
        strips = []
        for column in range(self.num_reels):
            strip = _reel_strip(self.symbols, symbol_weights)
            Random(20260913 + column).shuffle(strip)
            strips.append(strip)
        self.reels = {"DEV": strips}
        # N1 — pre-progression Fly mini-bonus (V0.3.8, DEV/PROVISIONAL; see
        # docs/milestones/V0.3.8_symbol_frequencies_fly_minibonus_20_40_60_80.md).
        # Rolled at most once per round, immediately after the initial board
        # is drawn and before its first evaluation (see activate_fly_minibonus
        # in gamestate.py) — not level-gated, since by design it fires before
        # any progression-driven feature could possibly exist yet. On
        # trigger, exactly one of `fly_wild_counts` (equal probability by
        # default) distinct board cells become temporary "WFLY" Wilds; the
        # board is then reevaluated immediately by run_spin's own first
        # evaluate_board() call, same no-removal/no-gravity-here pattern
        # already used by N3/N4. `fly_trigger_probability` is the primary
        # variable under study in V0.3.8 — disabled by default
        # (probability 0.0) so every pre-V0.3.8 config and every V0.3.8
        # config that does not explicitly opt in reproduces prior behaviour
        # byte-for-byte.
        #
        # V0.3.19 (installed 2026-09-25): the 7x7 production pairing enables
        # it at 0.057143 (~1/17.5, the exact value simulated in the V0.3.17
        # and V0.3.19 3,000,000-book validations) with (2, 3) Wilds, together
        # with the (10, 26, 40, 55) thresholds above. A WFLY Wild's own win
        # position earns no progression credit (V0.3.17, see
        # advance_progression in gamestate.py). Every other config (6x6
        # Candidate A, min6, DEV) keeps it disabled, byte-identical to before.
        if production and is_7x7:
            self.fly_minibonus_enabled = True
            self.fly_trigger_probability = 0.057143
            self.fly_wild_counts = (2, 3)
        else:
            self.fly_minibonus_enabled = False
            self.fly_trigger_probability = 0.0
            self.fly_wild_counts = (3, 4, 5)
        # `wincap` (V0.3.21) overrides the 1000x default for the Golden Mega
        # Fly study (10,000x target); production_config() leaves it at 1000x
        # until the product owner approves the final ladder.
        self.wincap = float(wincap) if wincap is not None else 1000.0  # Provisional ceiling, not a measured maximum.
        # Golden Mega Fly (V0.3.21, see GAME_DESIGN.md): rare N5 state whose
        # multiplier ladder (multiplier, weight) applies to the three boss
        # evaluations only. golden_enabled+golden_trigger_probability is the
        # in-simulation roll (used for self-consistency tests); the production
        # architecture instead uses the special GOLDEN/WINCAP distributions
        # below (force_golden) and class weights in publication.py.
        self.golden_ladder = tuple(golden_ladder) if golden_ladder else ()
        self.golden_trigger_probability = golden_trigger_probability
        self.golden_enabled = bool(self.golden_ladder) and golden_trigger_probability > 0
        self.golden_books = golden_books
        self.wincap_books = wincap_books
        self.max_cascades = 1000  # Diagnostic failure, never truncate/accept a round.
        # Declared RTP: written to the RGS configs (write_configs.py) and used
        # as the optimizer's target -- not a measured figure (the published
        # RTP is the exact weighted lookup-table value). The official SDK's
        # verify_mode_volatility() flags RTP > 0.967 as failing its "3-star
        # volatility limits", so the 7x7 pairing declares 0.965 (inside the
        # product owner's 96.0% floor / 96.3-96.7% target zone; measured
        # 96.501% for the installed V0.3.19 configuration) -- see
        # docs/milestones/V0.3.19_kitchen_7x7_fly_10_26_40_55_validation.md §9.1.
        # 6x6 Candidate A (historical, no longer the frontend/production
        # target) keeps its measured 0.969 (V0.3.9), above that limit.
        # DEV: plain placeholder.
        if production and is_7x7:
            self.rtp = 0.965
        elif production:
            self.rtp = 0.969
        else:
            self.rtp = 0.96
        self.opt_params = {}
        natural = Distribution(
            criteria="natural", quota=1.0,
            conditions={"reel_weights": {self.basegame_type: {"DEV": 1}}},
        )
        distributions = [natural]
        if golden_books or wincap_books:
            # NATURAL: ordinary rounds, Golden never rolls. GOLDEN: rounds
            # forced to start a Golden Mega Fly (rejection-sampled until N5
            # is reached). WINCAP: GOLDEN rounds whose final win is exactly
            # the wincap (SDK win_criteria + repeat, like games/0_0_cluster).
            reel = {"reel_weights": {self.basegame_type: {"DEV": 1}}, "force_golden": True}
            if golden_books:
                distributions.append(Distribution(criteria="golden", fixed_amt=golden_books, conditions=dict(reel)))
            if wincap_books:
                distributions.append(Distribution(criteria="wincap", fixed_amt=wincap_books,
                                                  win_criteria=self.wincap, conditions=dict(reel)))
        self.bet_modes = [
            BetMode(
                name="base", cost=1.0, rtp=self.rtp, max_win=self.wincap,
                auto_close_disabled=False, is_feature=False, is_buybonus=False,
                distributions=distributions,
            )
        ]
        if bonus_buy_books:
            # Bonus Buy (V0.3.22): a SECOND bet mode, appended after "base" so
            # the base mode (its distributions, RNG, books, lookup table) is
            # byte-for-byte what it was. One criteria draws complete natural
            # rounds that reach N5 without Golden (rejection-sampled by
            # GameState.check_repeat, `force_n5`); the Golden / 10,000x rounds
            # of this mode are the existing, verified GOLDEN/WINCAP base books
            # (publish_buy.py), whose production probability only comes from
            # lookup weights. `bonus_buy_criteria` only changes the seed offset
            # (string_to_int(criteria)), e.g. for an independent validation sample.
            self.bet_modes.append(BetMode(
                name=BONUS_BUY_MODE, cost=BONUS_BUY_COST, rtp=BONUS_BUY_RTP, max_win=self.wincap,
                auto_close_disabled=False, is_feature=False, is_buybonus=True,
                distributions=[Distribution(
                    criteria=bonus_buy_criteria, fixed_amt=bonus_buy_books,
                    conditions={"reel_weights": {self.basegame_type: {"DEV": 1}}, "force_n5": True},
                )],
            ))


# ---------------------------------------------------------------------------
# V0.3.21 production constants (approved 2026-10-03; see
# docs/milestones/V0.3.21_n5_fix_and_golden_mega_fly.md)
# ---------------------------------------------------------------------------
PRODUCTION_WINCAP = 10_000.0
# Golden Mega Fly ladder D: ONE multiplier is drawn when N5 triggers and the
# same value applies to the dedicated evaluation of all three boss sizes.
# Golden is a rare VARIANT of N5 (there is no N6). Exact probabilities.
GOLDEN_LADDER_PROBABILITIES = ((25, Fraction(36, 100)), (40, Fraction(55, 100)), (600, Fraction(9, 100)))
assert sum(p for _, p in GOLDEN_LADDER_PROBABILITIES) == 1
GOLDEN_LADDER = tuple((m, float(p)) for m, p in GOLDEN_LADDER_PROBABILITIES)
# Production probabilities, realised by lookup-table class weights (NOT by
# physical book counts). FINAL (product decision 2026-10-03, supersedes the
# first V0.3.21 install of 1 Golden per 16,634 N5 and a 1-in-20M cap):
#   P(Golden | N5) = 1 / 10,000 exactly (publication.class_weights_by_n5_rate),
#   P(10,000x | Golden) = 1954668/20000000 = 9.7734%, the cap share of a Golden
#   round given by the approved ladder on the corrected N5 payout distribution
#   (kept so the Golden mechanics/books are unchanged). With P(N5) ~ 1/117.5
#   this gives Golden ~ 1 in 1,175,084 rounds and 10,000x ~ 1 in 12.02M.
GOLDEN_PROBABILITY_PER_N5 = Fraction(1, 10_000)
CAP_GIVEN_GOLDEN = Fraction(1_954_668, 20_000_000)
# Physical library: 505,750 natural + 4,000 golden + 250 wincap = 510,000.
# Nothing in the SDK docs/verifier requires exactly 500,000 books (docs only
# recommend 100k+), so the validated 510,000 split is kept.
PRODUCTION_NATURAL_BOOKS = 505_750
PRODUCTION_GOLDEN_BOOKS = 4_000
PRODUCTION_WINCAP_BOOKS = 250


# ---------------------------------------------------------------------------
# V0.3.22 Bonus Buy (product decision 2026-10-12): one purchasable mode, a
# complete natural round N1 -> N5 for 100x the base bet, target RTP 96.5%.
# ---------------------------------------------------------------------------
BONUS_BUY_MODE = "bonus_buy"
BONUS_BUY_COST = 100.0
BONUS_BUY_RTP = 0.965
PRODUCTION_BONUS_BUY_NORMAL_BOOKS = 100_000


def production_config(golden_books=0, wincap_books=0, bonus_buy_books=0, bonus_buy_criteria="buy_n5"):
    """The Kitchen vs Fly production configuration: the 7x7 V0.3.19 pairing
    (see GameConfig's docstring) with the V0.3.21 N5 fix, wincap 10,000x and
    the Golden Mega Fly ladder. Single entry point shared by run.py (the
    development run), scripts/export_kitchen_frontend.py (the frontend
    playlist) and publish_golden.py (the production library), so all of them
    always build the same game. `golden_books`/`wincap_books` only add the
    special GOLDEN/WINCAP distributions used to build the production
    library; with the defaults (0) the config has the single NATURAL
    distribution and Golden never rolls (golden_trigger_probability 0).
    Bare `GameConfig(production=True)` is the historical 6x6 Candidate A."""
    config = GameConfig(production=True, num_reels=7, num_rows=7, wincap=PRODUCTION_WINCAP,
                        golden_ladder=GOLDEN_LADDER, golden_books=golden_books, wincap_books=wincap_books,
                        bonus_buy_books=bonus_buy_books, bonus_buy_criteria=bonus_buy_criteria)
    config.golden_probability_per_n5 = GOLDEN_PROBABILITY_PER_N5
    config.cap_given_golden = CAP_GIVEN_GOLDEN
    return config
