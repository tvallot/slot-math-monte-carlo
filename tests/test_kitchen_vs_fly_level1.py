"""Kitchen vs Fly Level 1 milestone: production config, no legacy features, progression.

These tests exercise the *production* `GameConfig()` defaults directly (no
per-test override of `n2_wild_enabled`), unlike `test_cascades_progression.py`'s
isolated `state` fixture. They check the specific Level 1 migration
requirements: eight Kitchen vs Fly symbols, historical fantasy features fully
removed, Kitchen-compatible progression stage names with no "ultimate" level
beyond N5, and that excess progression survives a threshold crossing.

Since V0.3.2, N2 (persistent Wilds) is itself an active Kitchen vs Fly
feature and is therefore enabled by default; since V0.3.3, N3 (premium
conversion) is enabled by default too; since V0.3.4, N4 (evolving 3x3
multiplier zone + temporary Wilds) is enabled by default as well; since
V0.3.5, N5 (Mega Wild boss) is enabled by default too, and its own
`bossTriggered`/`bossShrunk`/`bossDefeated` events are therefore expected,
not forbidden, once a production round reaches level 5. The *historical
fantasy* Armor, Boots, Sword and Dragon boss features have all been removed
from the codebase entirely. See `tests/test_kitchen_n2_persistent_wilds.py`,
`tests/test_kitchen_n3_premium_conversion.py`,
`tests/test_kitchen_n4_multiplier_zone.py` and
`tests/test_kitchen_n5_mega_wild_boss.py` for feature-specific coverage.
"""

from kitchen_vs_fly.game_config import GameConfig
from kitchen_vs_fly.gamestate import GameState
from kitchen_vs_fly.progression import ProgressionRules
from kitchen_vs_fly.validation import validate_book


def make_state():
    config = GameConfig()
    game = GameState(config)
    game.betmode = "base"
    game.criteria = "natural"
    return game


def test_production_config_has_eight_kitchen_symbols_four_low_four_premium():
    config = GameConfig()
    assert len(config.symbols) == 8
    low = [s for s in config.symbols if s.startswith("L")]
    premium = [s for s in config.symbols if s.startswith("H")]
    assert len(low) == 4 and len(premium) == 4
    assert set(config.symbol_display_names) == set(config.symbols)
    # V0.3.8 realigned this mapping so the narrative role matches the new
    # provisional paytable's tiers (Onion/Garlic cheapest lows, Oven Gloves
    # renamed from "oven_mitt"); a display-label change only, not a
    # technical rename (see game_config.py, GAME_DESIGN.md).
    assert [config.symbol_display_names[s] for s in low] == ["onion", "garlic", "butter", "herbs"]
    assert [config.symbol_display_names[s] for s in premium] == [
        "chef_knife", "roasting_dish", "oven_gloves", "roast_chicken",
    ]
    # Roast chicken is the narrative premium centerpiece: highest paytable
    # rank among the eight symbols (not a claim about its final value).
    ranks = {symbol: rank for rank, symbol in enumerate(config.symbols, start=1)}
    assert ranks["H4"] == max(ranks.values())


def test_old_fantasy_features_removed():
    config = GameConfig()
    assert not hasattr(config, "armor_enabled"), "Armor was removed in V0.3.2 (superseded by N2)"
    assert not hasattr(config, "boss_enabled"), "Dragon boss was removed in V0.3.5 (superseded by N5)"
    assert not hasattr(config, "boss_breath_patterns")
    assert not hasattr(config, "boots_enabled"), "Boots was removed entirely (Kitchen vs Fly cleanup)"
    assert not hasattr(config, "sword_enabled"), "Sword was removed entirely (Kitchen vs Fly cleanup)"


def test_n2_persistent_wilds_enabled_by_default():
    config = GameConfig()
    assert config.n2_wild_enabled is True
    assert isinstance(config.n2_wild_count, int) and config.n2_wild_count > 0


def test_n3_premium_conversion_enabled_by_default():
    config = GameConfig()
    assert config.n3_conversion_enabled is True
    assert config.premium_symbols == ("H1", "H2", "H3", "H4")
    assert set(config.premium_symbols) <= set(config.symbols)


def test_n4_multiplier_zone_enabled_by_default():
    config = GameConfig()
    assert config.n4_multiplier_enabled is True
    assert config.n4_zone_size == 3
    assert isinstance(config.n4_wild_count, int) and config.n4_wild_count > 0
    assert config.n4_multiplier_initial == 2
    assert config.n4_multiplier_cap == 5


def test_n5_boss_enabled_by_default():
    config = GameConfig()
    assert config.n5_boss_enabled is True
    assert config.n5_boss_size == 3


def test_progression_stage_names_are_kitchen_compatible_with_no_level_after_n5():
    config = GameConfig()
    stages = config.progression_rules.stages
    assert len(stages) == 5
    forbidden = {"adventurer", "boots", "armor", "sword", "ultimate"}
    assert not (set(stages) & forbidden)
    assert stages[-1] == "n5_boss"


def test_old_features_never_trigger_across_many_production_rounds():
    """Random production rounds, including ones that reach level 5, must never
    emit a legacy fantasy activation event (Boots/Armor/Sword — none of which
    the engine can even produce any more), and must still replay
    deterministically. bossTriggered is *not* forbidden here since V0.3.5: it
    is now the active N5 Mega Wild event, expected once a round reaches level
    5 (see test_kitchen_n5_mega_wild_boss.py)."""
    config = GameConfig()
    game = GameState(config)
    game.betmode = "base"
    game.criteria = "natural"
    max_level_seen = 1
    for sim in range(1000):
        game.run_spin(sim, simulation_seed=sim)
        book = game.book.to_json()
        event_types = {e["type"] for e in book["events"]}
        assert not event_types & {"bootsActivated", "armorActivated", "swordActivated"}
        max_level_seen = max(max_level_seen, game.level)
        validate_book(book, config)
    # Confirms the sample actually exercised threshold crossings, not just a
    # vacuous check on rounds that never left level 1.
    assert max_level_seen >= 2, "Scenario did not exercise any threshold crossing; widen the sample"


def test_progression_replay_is_deterministic_for_a_fixed_seed():
    game = make_state()
    game.run_spin(7, simulation_seed=123)
    first = game.book.to_json()
    game2 = make_state()
    game2.run_spin(7, simulation_seed=123)
    second = game2.book.to_json()
    assert first == second


def test_excess_progression_survives_a_threshold_crossing():
    """A single cascade gain that overshoots a threshold keeps the excess;
    it is not discarded or clamped at the threshold value."""
    game = make_state()
    game.config.progression_rules = ProgressionRules(thresholds=(3, 8, 20, 30))
    game.reset_book()
    wins = [{"positions": [{"reel": c, "row": 0} for c in range(5)]}]  # 5 distinct positions -> 5 points
    game.advance_progression(wins)
    assert game.progression == 5 and game.level == 2  # crossed 3, 2 points carried past it
    update = next(e for e in game.book.events if e["type"] == "progressUpdate")
    assert update["previousProgress"] == 0 and update["newProgress"] == 5 and update["delta"] == 5
    assert update["thresholdsReached"] == [3]
    assert update["nextThreshold"] == 8

    more_wins = [{"positions": [{"reel": c, "row": 1} for c in range(4)]}]  # 4 more points -> 9 total
    game.advance_progression(more_wins)
    assert game.progression == 9 and game.level == 3  # crossed 8 with 1 point carried past it
    second_update = [e for e in game.book.events if e["type"] == "progressUpdate"][-1]
    assert second_update["previousProgress"] == 5 and second_update["newProgress"] == 9
    assert second_update["thresholdsReached"] == [8]
    assert second_update["nextThreshold"] == 20


def test_progression_starts_at_zero_and_fully_resets_between_bets():
    game = make_state()
    game.config.progression_rules = ProgressionRules(thresholds=(2, 4, 6, 8))
    game.reset_book()
    assert game.progression == 0 and game.level == 1
    game.advance_progression([{"positions": [{"reel": 0, "row": 0}, {"reel": 1, "row": 0},
                                              {"reel": 2, "row": 0}]}])
    assert game.progression == 3 and game.level == 2
    game.reset_book()
    assert game.progression == 0 and game.level == 1
    assert not game.n2_wilds_activated and not game.n3_activated
    assert not game.n4_activated and not game.n4_multipliers
    assert not game.n5_boss_active and not game.n5_boss_defeated and not game.n5_boss_positions
