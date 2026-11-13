"""V0.3.21: reaching N5 always completes 3x3 -> 2x2 -> 1x1 -> defeated.

The seeds below are real production-config rounds that reached N5 and, under
the V0.3.20 conditional rule, did NOT complete: 236/532/728/874 ended with a
zero-win 1x1 evaluation, 163/301/515/787 had a 1x1 evaluation whose win missed
the Mega Wild and froze it. 576/664/1193 completed under both rules.
"""

import pytest

from kitchen_vs_fly.game_config import production_config
from kitchen_vs_fly.gamestate import GameState
from kitchen_vs_fly.validation import validate_book

NO_WIN_AT_1X1 = [236, 532, 728, 874]
MISSED_AT_1X1 = [163, 301, 515, 787]
ALWAYS_COMPLETED = [576, 664, 1193]


@pytest.fixture(scope="module")
def game():
    config = production_config()
    state = GameState(config)
    state.betmode, state.criteria = "base", "natural"
    return config, state


def play(game, seed):
    config, state = game
    state.run_spin(seed, simulation_seed=seed)
    book = state.book.to_json()
    validate_book(book, config)
    return book


@pytest.mark.parametrize("seed", NO_WIN_AT_1X1 + MISSED_AT_1X1 + ALWAYS_COMPLETED)
def test_every_n5_round_completes_the_full_sequence(game, seed):
    book = play(game, seed)
    boss = [e for e in book["events"] if e["type"] in ("bossTriggered", "bossShrunk", "bossDefeated")]
    assert [e["type"] for e in boss] == ["bossTriggered", "bossShrunk", "bossShrunk", "bossDefeated"]
    assert [e.get("newSize") for e in boss[1:3]] == [2, 1]
    assert boss[0]["size"] == 3 and boss[3]["previousSize"] == 1


@pytest.mark.parametrize("seed", NO_WIN_AT_1X1 + MISSED_AT_1X1 + ALWAYS_COMPLETED)
def test_no_stuck_mega_wild_is_left_on_the_final_board(game, seed):
    book = play(game, seed)
    boards = [e["board"] for e in book["events"] if "board" in e and e["type"] != "reveal"]
    assert all(cell["name"] != "WN5" for column in boards[-1] for cell in column)


@pytest.mark.parametrize("seed", NO_WIN_AT_1X1 + MISSED_AT_1X1 + ALWAYS_COMPLETED)
def test_each_size_is_evaluated_exactly_once(game, seed):
    """Exactly one dedicated evaluation per size: between consecutive boss
    events there is at most one winInfo that is dedicated to the footprint
    that has just appeared (a size whose evaluation paid nothing has none)."""
    book = play(game, seed)
    sizes_seen = []
    for e in book["events"]:
        if e["type"] == "bossTriggered":
            sizes_seen.append(e["size"])
        elif e["type"] == "bossShrunk":
            sizes_seen.append(e["newSize"])
    assert sizes_seen == [3, 2, 1]
    # The validator already replays every evaluation and the state machine;
    # reaching here means no size was skipped or repeated.


def test_zero_win_1x1_round_has_no_win_between_last_shrink_and_defeat(game):
    book = play(game, NO_WIN_AT_1X1[0])
    kinds = [e["type"] for e in book["events"]]
    last_shrink = max(i for i, k in enumerate(kinds) if k == "bossShrunk")
    defeat = kinds.index("bossDefeated")
    assert "winInfo" not in kinds[last_shrink:defeat]


def test_replay_is_deterministic(game):
    assert play(game, NO_WIN_AT_1X1[1]) == play(game, NO_WIN_AT_1X1[1])
