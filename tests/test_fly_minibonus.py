"""Fly mini-bonus (V0.3.8, pre-N1 base-game feature): trigger, temporary
Wild placement/consumption, determinism and replay.

Rolled at most once per round, right after the initial board is drawn and
before the round's very first evaluation (see run_spin/activate_fly_minibonus
in gamestate.py) — not level-gated, since by design it fires before any
progression-driven feature could possibly have unlocked yet. On trigger,
`config.fly_wild_counts` (3, 4 or 5, equal probability by default) distinct
cells become temporary "WFLY" Wilds. A WFLY Wild needs no dedicated
explode/gravity bookkeeping: it is an ordinary, non-sticky board symbol, so
the engine's normal explode/gravity path removes it automatically the
moment it takes part in a paid win, exactly like N4's own "WN4" Wild minus
the persistent multiplier state N4 also has to track. It DOES need
dedicated progression bookkeeping, though (V0.3.17): a WFLY win position's
progression credit is deliberately withheld, the same way N2 sticky/N5
boss positions already are — see
docs/milestones/V0.3.17_kitchen_7x7_fly_minibonus.md for why.
"""

from collections import Counter

import pytest

from kitchen_vs_fly.progression import ProgressionRules
from kitchen_vs_fly.validation import validate_book
from tests.test_cascades_progression import state, quiet_board, set_positions, controlled_draw
from src.events.events import reveal_event


@pytest.fixture
def fly_state(state):
    state.config.fly_minibonus_enabled = True
    state.config.fly_trigger_probability = 1.0
    return state


def fly_events(state):
    return [e for e in state.book.events if e["type"] == "flyMiniBonusTriggered"]


def force_trigger(monkeypatch, triggered):
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.random", lambda: 0.0 if triggered else 1.0)


def force_count(monkeypatch, count):
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", lambda seq: count)


def force_sample(monkeypatch, selected):
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample", lambda seq, k: selected)


def test_disabled_by_default_no_event_no_rng(state, monkeypatch):
    assert not state.config.fly_minibonus_enabled
    state.draw_board()

    def unexpected(*args, **kwargs):
        raise AssertionError("Fly must not consume RNG when disabled")

    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.random", unexpected)
    state.activate_fly_minibonus()
    assert not fly_events(state)
    assert state.fly_minibonus_activated is False  # disabled path returns before setting the flag


def test_miss_still_emits_one_event_with_no_wilds(fly_state, monkeypatch):
    fly_state.config.fly_trigger_probability = 0.5
    fly_state.draw_board()
    force_trigger(monkeypatch, triggered=False)

    def unexpected(*args, **kwargs):
        raise AssertionError("A missed roll must not draw a Wild count or placement")

    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.choice", unexpected)
    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.sample", unexpected)
    board_before = [[dict(name=s.name) for s in col] for col in fly_state.board]
    fly_state.activate_fly_minibonus()
    events = fly_events(fly_state)
    assert len(events) == 1
    event = events[0]
    assert event["triggered"] is False
    assert event["wildCount"] == 0
    assert event["positions"] == []
    assert event["level"] == 1 and event["progression"] == 0 and event["cascade"] == 0
    board_after = [[dict(name=s.name) for s in col] for col in fly_state.board]
    assert board_after == board_before


def test_activates_only_once_per_round(fly_state, monkeypatch):
    fly_state.draw_board()
    force_trigger(monkeypatch, triggered=True)
    force_count(monkeypatch, 3)
    force_sample(monkeypatch, [(0, 0), (1, 1), (2, 2)])
    fly_state.activate_fly_minibonus()
    assert len(fly_events(fly_state)) == 1

    def unexpected(*args, **kwargs):
        raise AssertionError("Fly must not roll twice in the same round")

    monkeypatch.setattr("kitchen_vs_fly.gamestate.random.random", unexpected)
    fly_state.activate_fly_minibonus()
    assert len(fly_events(fly_state)) == 1


@pytest.mark.parametrize("count", [3, 4, 5])
def test_trigger_places_exactly_the_configured_count_of_distinct_wilds(fly_state, monkeypatch, count):
    fly_state.draw_board()
    original = [[symbol.name for symbol in column] for column in fly_state.board]
    force_trigger(monkeypatch, triggered=True)
    force_count(monkeypatch, count)
    selected = [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4)][:count]
    force_sample(monkeypatch, selected)
    fly_state.activate_fly_minibonus()
    event = fly_events(fly_state)[0]
    assert event["triggered"] is True
    assert event["wildCount"] == count
    positions = [(p["reel"], p["row"]) for p in event["positions"]]
    assert positions == sorted(selected)
    assert len(positions) == len(set(positions)) == count
    for c, r in selected:
        assert fly_state.board[c][r].name == "WFLY"
        assert fly_state.board[c][r].wild is True
    for c, column in enumerate(fly_state.board):
        for r, symbol in enumerate(column):
            if (c, r) not in selected:
                assert symbol.name == original[c][r]
    for change in event["positions"]:
        c, r = change["reel"], change["row"]
        assert change["fromSymbol"] == original[c][r] and change["toSymbol"] == "WFLY"


def test_wild_count_selection_is_approximately_equal_over_many_rolls(fly_state, monkeypatch):
    """Statistical check for the required 1/3-1/3-1/3 split (section 28 of
    the V0.3.8 brief): real RNG, many independent rolls, no monkeypatch on
    random.choice itself — only the trigger is forced so every roll reaches
    the count draw."""
    force_trigger(monkeypatch, triggered=True)
    counts = Counter()
    for sim in range(3000):
        fly_state.reset_book()
        fly_state.draw_board()
        fly_state.activate_fly_minibonus()
        counts[fly_events(fly_state)[0]["wildCount"]] += 1
    assert set(counts) == {3, 4, 5}
    for count in (3, 4, 5):
        # Expected ~1000 each; a wide tolerance keeps this non-flaky while
        # still catching an actually-biased implementation.
        assert 800 <= counts[count] <= 1200, counts


def test_wilds_participate_in_cluster_and_are_consumed_on_win(fly_state, monkeypatch):
    """Four cells of a 4-cell near-cluster plus one Fly Wild completes a
    5-symbol win; the Wild is removed like any normal symbol (not sticky).

    Background uses 4 distinct symbols cycling by (c+r)%4 (same pattern as
    controlled_draw_fifteen_cluster in test_kitchen_n4_multiplier_zone.py):
    any two orthogonal neighbours differ, and a lone Wild only ever bridges
    at most 2 same-symbol neighbours on each side (sums S-1/S+1 mod 4), so
    it can never reach min_cluster_size (5) by itself — unlike a plain
    2-symbol checkerboard background, where a lone Wild's 4 same-parity
    neighbours alone already hit exactly 5."""
    background = ("L3", "L4", "H2", "H3")
    fly_state.board = [[fly_state.create_symbol(background[(c + r) % 4]) for r in range(6)] for c in range(6)]
    set_positions(fly_state, [(0, 0), (0, 1), (0, 2), (0, 3)], "H1")
    fly_state.reelstrip = [list(background) for _ in range(6)]
    fly_state.reel_positions = [0] * 6
    force_trigger(monkeypatch, triggered=True)
    force_count(monkeypatch, 3)
    force_sample(monkeypatch, [(0, 4), (2, 2), (3, 3)])
    fly_state.activate_fly_minibonus()
    assert fly_state.board[0][4].name == "WFLY"
    fly_state.evaluate_board()
    assert fly_state.win_data["totalWin"] > 0
    win_positions = {(p["reel"], p["row"]) for win in fly_state.win_data["wins"] for p in win["positions"]}
    assert (0, 4) in win_positions
    fly_state.tumble_board()
    assert fly_state.board[0][4].name != "WFLY", "a participating Fly Wild must be removed, not sticky"


def test_wild_not_participating_persists_for_a_later_cascade(fly_state, monkeypatch):
    """Three scattered, mutually non-adjacent Fly Wilds on the same
    4-symbol-cycling background as the test above (see its docstring for
    why a lone Wild cannot self-pay there)."""
    background = ("L3", "L4", "H2", "H3")
    fly_state.board = [[fly_state.create_symbol(background[(c + r) % 4]) for r in range(6)] for c in range(6)]
    fly_state.reelstrip = [list(background) for _ in range(6)]
    fly_state.reel_positions = [0] * 6
    force_trigger(monkeypatch, triggered=True)
    force_count(monkeypatch, 3)
    force_sample(monkeypatch, [(0, 0), (2, 3), (4, 5)])
    fly_state.activate_fly_minibonus()
    fly_state.evaluate_board()
    assert fly_state.win_data["totalWin"] == 0, "isolated Fly Wilds on a quiet background must not self-pay"
    for c, r in [(0, 0), (2, 3), (4, 5)]:
        assert fly_state.board[c][r].name == "WFLY"


def test_progression_withholds_credit_for_a_consumed_fly_wild(fly_state, monkeypatch):
    """V0.3.17: a WFLY Wild's own win position is excluded from progression
    credit, the same way N2 sticky/N5 boss positions are (see gamestate.py,
    advance_progression) — even though, unlike those, it IS actually removed
    like any ordinary symbol. This is a deliberate design decision, not a
    technical necessity: screening showed that crediting Fly wins normally
    let 3-5 extra Wilds placed on the pristine initial board blow through
    several progression thresholds in a single round, pushing 7x7 RTP from
    ~96.6% to ~140-150% at a 1-in-17.5 trigger rate (see
    docs/milestones/V0.3.17_kitchen_7x7_fly_minibonus.md)."""
    fly_state.config.progression_rules = ProgressionRules(thresholds=(1, 100, 200, 300))
    background = ("L3", "L4", "H2", "H3")  # see test above: a lone Wild cannot self-pay on this background
    fly_state.board = [[fly_state.create_symbol(background[(c + r) % 4]) for r in range(6)] for c in range(6)]
    set_positions(fly_state, [(0, 0), (0, 1), (0, 2), (0, 3)], "H1")
    fly_state.reelstrip = [list(background) for _ in range(6)]
    fly_state.reel_positions = [0] * 6
    force_trigger(monkeypatch, triggered=True)
    force_count(monkeypatch, 3)
    force_sample(monkeypatch, [(0, 4), (2, 2), (3, 3)])
    fly_state.activate_fly_minibonus()
    fly_state.evaluate_board()
    wins = fly_state.win_data["wins"]
    assert fly_state.fly_wild_win_positions == {(0, 4)}
    fly_state.tumble_board()
    assert fly_state.board[0][4].name != "WFLY", "the consumed Fly Wild must still explode/refill normally"
    fly_state.advance_progression(wins)
    assert fly_state.progression == 4, "only the 4 H1 positions are credited; the consumed WFLY position is not"


def test_reset_between_books_clears_flag_and_board(fly_state, monkeypatch):
    monkeypatch.setattr(fly_state, "draw_board", lambda: controlled_draw(fly_state))
    force_trigger(monkeypatch, triggered=True)
    force_count(monkeypatch, 3)
    fly_state.config.fly_trigger_probability = 1.0
    fly_state.run_spin(0)
    assert fly_state.fly_minibonus_activated is True
    fly_state.run_spin(1)
    assert fly_state.fly_minibonus_activated is True  # reset then re-activated within the same spin
    assert len(fly_events(fly_state)) == 1


def test_does_not_corrupt_n2_n4_n5_state(state, monkeypatch):
    """Fly fires before anything else can possibly exist (see run_spin), so
    right after activate_fly_minibonus() every other feature's tracking
    state must still be completely untouched — checked directly rather than
    after the full round, since a later feature legitimately reusing a
    now-empty ex-Fly cell is not corruption (see the full-round replay test
    below for that combined scenario)."""
    state.config.fly_minibonus_enabled = True
    state.config.fly_trigger_probability = 1.0
    state.config.n2_wild_enabled = True
    state.config.n4_multiplier_enabled = True
    state.config.n5_boss_enabled = True
    state.reset_seed(0)
    state.reset_book()
    state.draw_board()
    state.activate_fly_minibonus()
    activation = fly_events(state)[0]
    assert activation["triggered"] is True
    fly_positions = {(p["reel"], p["row"]) for p in activation["positions"]}
    assert fly_positions
    assert state.sticky_positions == set()
    assert state.n4_multipliers == {}
    assert state.n5_boss_positions == set()
    for c, r in fly_positions:
        assert state.board[c][r].name == "WFLY"


def test_fly_then_n2_n4_n5_full_round_replays(state, monkeypatch):
    """controlled_draw's own vertical L1 cluster reaches every DEV threshold
    through ordinary cascades regardless of Fly, so N2/N3/N4/N5 legitimately
    activate later in the SAME round here — Fly firing first must not
    prevent or corrupt that; validate_book independently re-derives the
    whole round from the book's own events."""
    state.config.fly_minibonus_enabled = True
    state.config.fly_trigger_probability = 1.0
    state.config.n2_wild_enabled = True
    state.config.n3_conversion_enabled = True
    state.config.n4_multiplier_enabled = True
    state.config.n5_boss_enabled = True
    monkeypatch.setattr(state, "draw_board", lambda: controlled_draw(state))
    state.run_spin(0)
    assert fly_events(state)[0]["triggered"] is True
    book = state.book.to_json()
    validate_book(book, state.config)


def test_full_replay_validates_triggered_and_missed_books(fly_state, monkeypatch):
    monkeypatch.setattr(fly_state, "draw_board", lambda: controlled_draw(fly_state))
    fly_state.config.fly_trigger_probability = 1.0
    force_trigger(monkeypatch, triggered=True)
    force_count(monkeypatch, 3)
    force_sample(monkeypatch, [(1, 5), (2, 5), (3, 5)])
    fly_state.run_spin(0)
    book = fly_state.book.to_json()
    validate_book(book, fly_state.config)

    fly_state.config.fly_trigger_probability = 0.0
    force_trigger(monkeypatch, triggered=False)
    fly_state.run_spin(1)
    book_missed = fly_state.book.to_json()
    validate_book(book_missed, fly_state.config)


def test_replay_rejects_a_dropped_fly_event(fly_state, monkeypatch):
    monkeypatch.setattr(fly_state, "draw_board", lambda: controlled_draw(fly_state))
    force_trigger(monkeypatch, triggered=True)
    force_count(monkeypatch, 3)
    force_sample(monkeypatch, [(1, 5), (2, 5), (3, 5)])
    fly_state.run_spin(0)
    book = fly_state.book.to_json()
    corrupted = dict(book)
    corrupted["events"] = [e for e in book["events"] if e["type"] != "flyMiniBonusTriggered"]
    for i, event in enumerate(corrupted["events"]):
        event["index"] = i
    with pytest.raises(AssertionError):
        validate_book(corrupted, fly_state.config)


def test_replay_rejects_wrong_wild_count_in_event(fly_state, monkeypatch):
    monkeypatch.setattr(fly_state, "draw_board", lambda: controlled_draw(fly_state))
    force_trigger(monkeypatch, triggered=True)
    force_count(monkeypatch, 3)
    force_sample(monkeypatch, [(1, 5), (2, 5), (3, 5)])
    fly_state.run_spin(0)
    book = fly_state.book.to_json()
    for event in book["events"]:
        if event["type"] == "flyMiniBonusTriggered":
            event["wildCount"] = 999
    with pytest.raises(AssertionError):
        validate_book(book, fly_state.config)
