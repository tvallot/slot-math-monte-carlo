"""V0.3.19 -- the Kitchen vs Fly production configuration, installed 2026-09-25.

`GameConfig(production=True, num_reels=7, num_rows=7)` is the configuration
the frontend exports and plays (see scripts/export_kitchen_frontend.py). It
must be exactly the configuration validated on 3,000,000 books in
docs/milestones/V0.3.19_kitchen_7x7_fly_10_26_40_55_validation.md.
"""

from kitchen_vs_fly.game_config import (
    KITCHEN_7X7_RTP96_PAYTABLE_STEPS,
    KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS,
    GameConfig,
    _step_paytable,
)
from kitchen_vs_fly.gamestate import GameState
from kitchen_vs_fly.validation import validate_book


def production():
    return GameConfig(production=True, num_reels=7, num_rows=7)


def test_grid_is_7x7_with_49_cells_and_min_cluster_5():
    config = production()
    assert config.num_reels == 7
    assert config.num_rows == [7] * 7
    assert config.num_reels * config.num_rows[0] == 49
    assert config.min_cluster_size == 5
    assert max(size for size, _symbol in config.paytable) == 49


def test_thresholds_fly_and_wild_counts_are_the_v0319_values():
    config = production()
    assert config.progression_rules.thresholds == (10, 26, 40, 55)
    assert config.fly_minibonus_enabled is True
    assert config.fly_trigger_probability == 0.057143
    assert abs(config.fly_trigger_probability - 1 / 17.5) < 1e-6
    assert config.fly_wild_counts == (2, 3)
    assert config.n2_wild_count == 2
    assert config.n2_temp_wild_count == 2
    assert config.n4_wild_count == 3
    assert config.n4_zone_size == 3
    assert config.n5_boss_size == 3


def test_symbol_weights_are_pairs_a():
    assert KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS == {
        "L1": 19, "L2": 19, "L3": 15, "L4": 15, "H1": 11, "H2": 9, "H3": 7, "H4": 5,
    }
    config = production()
    counts = {s: sum(column.count(s) for column in config.reels["DEV"]) for s in config.symbols}
    ratios = [counts[s] / counts["H4"] for s in config.symbols]
    expected = [KITCHEN_7X7_RTP96_SYMBOL_WEIGHTS[s] / 5 for s in config.symbols]
    assert all(abs(r - e) < 0.05 for r, e in zip(ratios, expected))


def test_paytable_is_the_v0318_table_with_its_three_roundings():
    config = production()
    assert KITCHEN_7X7_RTP96_PAYTABLE_STEPS["H1"][30] == 39.00
    assert KITCHEN_7X7_RTP96_PAYTABLE_STEPS["H3"][30] == 130.00
    assert KITCHEN_7X7_RTP96_PAYTABLE_STEPS["H4"][30] == 260.00
    assert config.paytable == _step_paytable(config.symbols, KITCHEN_7X7_RTP96_PAYTABLE_STEPS, 49, min_size=5)
    assert config.paytable[(49, "H4")] == 260.00


def test_declared_rtp_and_wincap():
    config = production()
    assert config.rtp == 0.965
    assert config.bet_modes[0].get_rtp() == 0.965
    assert config.wincap == 1000.0  # bare GameConfig keeps the V0.3.19 default
    from kitchen_vs_fly.game_config import production_config
    installed = production_config()  # V0.3.21: Golden Mega Fly max win
    assert installed.wincap == 10_000.0 and installed.bet_modes[0].get_wincap() == 10_000.0
    assert installed.rtp == 0.965


def test_other_configs_keep_their_historical_values():
    six = GameConfig(production=True)  # 6x6 Candidate A: history/tests only
    assert six.num_reels == 6 and six.progression_rules.thresholds == (20, 40, 60, 80)
    assert six.fly_minibonus_enabled is False and six.fly_trigger_probability == 0.0
    assert GameConfig().fly_minibonus_enabled is False


def test_production_books_replay_and_wfly_wins_earn_no_progression_credit():
    """Natural production books: every book passes the independent replay
    validator (which re-derives progression with the V0.3.17 WFLY exclusion),
    and the first win of a Fly round credits only its non-WFLY positions."""
    config = production()
    game = GameState(config)
    game.betmode, game.criteria = "base", "natural"
    checked_wfly_wins = 0
    for sim in range(1500):
        game.run_spin(sim, simulation_seed=sim)
        book = game.book.to_json()
        validate_book(book, config)
        events = book["events"]
        fly = next(e for e in events if e["type"] == "flyMiniBonusTriggered")
        if not fly["triggered"]:
            continue
        assert fly["wildCount"] in (2, 3)
        first_win = next((i for i, e in enumerate(events) if e["type"] == "winInfo"), None)
        if first_win is None:
            continue
        fly_cells = {(p["reel"], p["row"]) for p in fly["positions"]}
        won = {(p["reel"], p["row"]) for w in events[first_win]["wins"] for p in w["positions"]}
        if not won & fly_cells:
            continue
        update = next(e for e in events[first_win:] if e["type"] == "progressUpdate")
        assert update["delta"] == len(won - fly_cells)
        checked_wfly_wins += 1
    assert checked_wfly_wins > 0


def test_production_config_entry_point_is_the_7x7_v0319_pairing():
    from kitchen_vs_fly.game_config import production_config
    config = production_config()
    assert config.num_reels == 7 and config.num_rows == [7] * 7
    assert config.progression_rules.thresholds == (10, 26, 40, 55)
    assert config.fly_minibonus_enabled and config.rtp == 0.965


def test_run_py_builds_the_publishable_library_from_production_config():
    from pathlib import Path
    source = Path(__file__).resolve().parents[1].joinpath("kitchen_vs_fly/run.py").read_text()
    assert "config = production_config()" in source
    assert "GameConfig(production=True)" not in source
