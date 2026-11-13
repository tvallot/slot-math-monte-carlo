"""Replay all development books without running the game or using SDK clusters."""

import csv
import io
import json
from pathlib import Path

import zstandard


WILD_NAMES = ("W", "WN2T", "WN4", "WN5", "WFLY")  # N2/N4/N5/Fly Wild cells all match any cluster.


def grid_dims(config):
    """(num_reels, num_rows) for the independent replay verification below.

    Added for the V0.3.6 6x6-vs-6x7 grid study (see
    docs/milestones/V0.3.6_grid_6x6_vs_6x7_study.md): this module used to
    hardcode 6 for both dimensions throughout, which is only correct for the
    original square 6x6 grid. GameConfig only ever builds a uniform-height
    grid (see game_config.py), so num_rows is asserted uniform here rather
    than generalized to a per-reel list — nothing in Kitchen vs Fly needs
    that, and doing so would add untested surface for no current use."""
    assert len(set(config.num_rows)) == 1, "validation.py assumes a uniform row count across reels"
    return config.num_reels, config.num_rows[0]


def n5_candidate_placements(size, sticky, reels, rows):
    """All valid top-left corners for a `size`x`size` footprint on a
    `reels`x`rows` grid that never overlaps a current N2 sticky (persistent
    Wild) position. Independent reimplementation of
    GameState.n5_boss_candidate_placements, used only for replay verification."""
    max_c, max_r = reels - size, rows - size
    placements = []
    for c in range(max_c + 1):
        for r in range(max_r + 1):
            footprint = {(x, y) for x in range(c, c + size) for y in range(r, r + size)}
            if footprint.isdisjoint(sticky):
                placements.append((c, r))
    return sorted(placements)


def n5_footprint(top_left, size):
    c, r = top_left
    return {(x, y) for x in range(c, c + size) for y in range(r, r + size)}


def n5_min_overlap_with_n4(size, candidates, n4_positions):
    """Minimum number of cells any candidate placement is forced to share
    with the N4 zone; 0 whenever a fully non-overlapping candidate exists.
    Used to verify the initial 3x3 boss placement never accepts a placement
    with avoidable overlap (see GAME_DESIGN.md and
    docs/milestones/V0.3.5_kitchen_n5_mega_wild_fly.md for the proof that a
    zero-overlap candidate does not always exist)."""
    if not n4_positions:
        return 0
    return min(len(n5_footprint(p, size) & n4_positions) for p in candidates)


def winning_clusters(board, config, multipliers=None, global_multiplier=1):
    """Independent iterative flood fill, used only for verification.

    `multipliers` is an optional {(reel, row): value} snapshot of N4
    position-owned multiplier state *before* this evaluation's upgrades: the
    per-cluster payout sums the values of participating positions (additive
    V1 rule, floored at 1), exactly mirroring src.calculations.cluster's own
    multiplier_key handling.
    """
    reels, rows = grid_dims(config)
    multipliers = multipliers or {}
    wins = set()
    # global_multiplier is the Golden Mega Fly multiplier of a dedicated boss
    # evaluation (1 everywhere else); it multiplies the whole cluster payout.
    # Evaluate each ordinary symbol independently; Wilds may belong to several
    # differently named clusters, but pure Wild components have no paytable entry.
    for symbol in config.symbols:
        seen = set()
        for column in range(reels):
            for row in range(rows):
                if (column, row) in seen or board[column][row]["name"] != symbol:
                    continue
                pending = [(column, row)]
                positions = set()
                while pending:
                    c, r = pending.pop()
                    if (c, r) in seen or board[c][r]["name"] not in (symbol, *WILD_NAMES):
                        continue
                    seen.add((c, r))
                    positions.add((c, r))
                    pending.extend((x, y) for x, y in ((c-1, r), (c+1, r), (c, r-1), (c, r+1))
                                   if 0 <= x < reels and 0 <= y < rows)
                if len(positions) >= config.min_cluster_size:
                    cluster_mult = sum(v for p, v in multipliers.items() if p in positions and v > 0)
                    cluster_mult = max(cluster_mult, 1)
                    base = config.paytable[len(positions), symbol]
                    wins.add((symbol, frozenset(positions), round(base * cluster_mult * global_multiplier * 100), cluster_mult))
    return wins


def validate_book(book, config):
    reels, rows = grid_dims(config)
    board = None
    pending = set()
    total = 0
    cascades = 0
    capped = False
    rules = config.progression_rules
    progress, level = 0, 1
    update_reason = None
    expected_gain = 0
    expected_levels = []
    start_seen = False
    n2_due = n2_used = False
    n3_due = n3_used = False
    n4_due = n4_used = n4_upgrade_due = False
    expected_n4_changes = []
    n5_due = n5_used = n5_active = n5_defeated = False
    n5_size = n5_anchor = None
    n5_positions = set()
    n5_awaiting_evaluation = False
    n5_relocate_due = n5_tumble_pending = False
    n5_advance_this_evaluation = False
    n5_just_triggered = False
    golden_active, golden_multiplier = False, 1
    n5_expected_new_size = None
    n5_board_expected = None
    n5_entered_positions = set()
    n5_next_size = n5_next_anchor = n5_next_positions = None
    n5_next_defeated = False
    sticky = set()
    n4_multipliers = {}
    fly_pending = False
    events = book["events"]
    assert events[0]["type"] == "reveal"
    assert events[-1]["type"] == "finalWin"
    assert [event["index"] for event in events] == list(range(len(events)))
    for event in events:
        kind = event["type"]
        if fly_pending:
            assert kind == "flyMiniBonusTriggered", "Missing or reordered Fly mini-bonus roll"
        elif update_reason is not None:
            assert kind == "progressUpdate", "Missing or reordered progression update"
        if expected_levels:
            assert kind == "levelUp", "Missing or reordered level transition"
        elif n2_due:
            assert kind == "wildAdded", "Missing or reordered N2 Wild activation"
        elif n3_due:
            assert kind == "gridTransform", "Missing or reordered N3 premium conversion"
        elif n4_due:
            assert kind == "multiplierZoneCreated", "Missing or reordered N4 zone activation"
        elif n4_upgrade_due:
            assert kind == "multiplierUpgraded", "Missing or reordered N4 multiplier upgrade"
        elif n5_due:
            assert kind == "bossTriggered", "Missing or reordered N5 boss activation"
        elif n5_relocate_due:
            assert kind in ("bossShrunk", "bossDefeated"), "Missing or reordered N5 boss shrink/defeat"
        elif n5_tumble_pending:
            assert kind == "tumbleBoard", "Missing N5 boss relocation/defeat tumble"
        if kind != "goldenFlyStarted":
            n5_just_triggered = n5_just_triggered and kind == "bossTriggered"
        if kind == "reveal":
            assert board is None
            board = event["board"]
            if config.fly_minibonus_enabled:
                fly_pending = True
            else:
                update_reason = "roundStart"
        elif kind == "flyMiniBonusTriggered":
            assert fly_pending and not capped
            assert event["level"] == 1 and event["progression"] == progress == 0
            assert event["cascade"] == cascades == 0
            if not event["triggered"]:
                assert event["wildCount"] == 0 and event["positions"] == []
                assert event["board"] == board
            else:
                assert event["wildCount"] in config.fly_wild_counts
                changes = event["positions"]
                positions = [(p["reel"], p["row"]) for p in changes]
                assert len(positions) == len(set(positions)) == event["wildCount"]
                assert all(0 <= c < reels and 0 <= r < rows for c, r in positions)
                next_board = [[dict(s) for s in col] for col in board]
                for change in changes:
                    c, r = change["reel"], change["row"]
                    assert change["fromSymbol"] == board[c][r]["name"] and change["toSymbol"] == "WFLY"
                    next_board[c][r] = {"name": "WFLY", "wild": True}
                assert next_board == event["board"]
                board = next_board
            fly_pending = False
            update_reason = "roundStart"
        elif kind == "progressUpdate":
            assert update_reason is not None
            assert event["reason"] == update_reason and event["cascade"] == cascades
            gain = 0 if update_reason == "roundStart" else expected_gain
            new_progress = progress + gain
            new_level = 1 + sum(new_progress >= t for t in rules.thresholds)
            assert event["previousProgress"] == progress and event["newProgress"] == new_progress
            assert event["delta"] == gain
            assert event["previousLevel"] == level and event["newLevel"] == new_level
            assert event["thresholdsReached"] == list(rules.thresholds[level - 1:new_level - 1])
            assert event["nextThreshold"] == (rules.thresholds[new_level - 1]
                                              if new_level <= len(rules.thresholds) else None)
            if update_reason == "roundStart":
                assert not start_seen and event["levels"] == rules.levels()
                start_seen = True
            expected_levels = [(progress, target) for target in range(level + 1, new_level + 1)]
            n2_due = config.n2_wild_enabled and new_level >= 2 and not n2_used
            n3_due = config.n3_conversion_enabled and new_level >= 3 and not n3_used
            n4_due = config.n4_multiplier_enabled and new_level >= 4 and not n4_used
            n5_due = config.n5_boss_enabled and new_level >= 5 and not n5_used
            # Promote a hit recorded by the winInfo just resolved by this very
            # tumble/progressUpdate to the guard that expects
            # bossShrunk/bossDefeated, now that this cascade's normal
            # tumbleBoard/progressUpdate/levelUp sequence is complete —
            # exactly where gamestate.py's activate_n5_boss() itself runs.
            if n5_advance_this_evaluation:
                n5_relocate_due = True
                n5_expected_new_size = n5_size - 1
                n5_advance_this_evaluation = False
            progress, level = new_progress, new_level
            update_reason = None
        elif kind == "levelUp":
            assert expected_levels, "Duplicate or unexpected level transition"
            old_progress, target = expected_levels.pop(0)
            assert event["previousProgress"] == old_progress and event["newProgress"] == progress
            assert event["previousLevel"] == target - 1 and event["newLevel"] == target
            assert event["thresholdReached"] == rules.thresholds[target - 2]
            assert event["stage"] == rules.stages[target - 1]
            assert event["isFinalLevel"] == (target == len(rules.stages))
            assert event["cascade"] == cascades
        elif kind == "wildAdded":
            assert n2_due and not n2_used and not pending and not capped
            assert event["level"] == 2 and event["progression"] == progress
            assert event["cascade"] == cascades
            eligible = {(c, r) for c in range(reels) for r in range(rows)
                        if board[c][r]["name"] in config.symbols and (c, r) not in sticky}
            changes = event["positions"]
            positions = {(p["reel"], p["row"]) for p in changes}
            assert len(positions) == len(changes) == event["wildCount"]
            assert len(positions) == min(config.n2_wild_count, len(eligible))
            assert positions <= eligible
            next_board = [[dict(s) for s in col] for col in board]
            for change in changes:
                c, r = change["reel"], change["row"]
                assert change["fromSymbol"] == board[c][r]["name"] and change["toSymbol"] == "W"
                next_board[c][r] = {"name": "W", "wild": True, "sticky": True}
            # Optional "mixed Wilds" variant (config.n2_temp_wild_count > 0):
            # temporary, non-sticky WN2T Wilds placed among the remaining
            # eligible positions (never among the sticky ones just chosen
            # above), same event-shape convention as N4/Fly's own temporary
            # Wilds — present in the event only when there is at least one.
            temp_eligible = eligible - positions
            expected_temp_count = min(config.n2_temp_wild_count, len(temp_eligible))
            temp_changes = event.get("temporaryPositions", [])
            temp_positions = {(p["reel"], p["row"]) for p in temp_changes}
            assert len(temp_positions) == len(temp_changes) == event.get("temporaryWildCount", 0)
            assert len(temp_positions) == expected_temp_count
            assert temp_positions <= temp_eligible
            for change in temp_changes:
                c, r = change["reel"], change["row"]
                assert change["fromSymbol"] == board[c][r]["name"] and change["toSymbol"] == "WN2T"
                next_board[c][r] = {"name": "WN2T", "wild": True}
            sticky |= positions
            assert event["stickyPositions"] == [{"reel": c, "row": r} for c, r in sorted(sticky)]
            assert next_board == event["board"]
            board = next_board
            n2_used, n2_due = True, False
        elif kind == "gridTransform":
            assert n3_due and not n3_used and not pending and not capped
            assert event["level"] == 3 and event["progression"] == progress
            assert event["cascade"] == cascades
            present = sorted({board[c][r]["name"] for c in range(reels) for r in range(rows)
                               if board[c][r]["name"] in config.premium_symbols})
            assert event["premiumTypesPresent"] == present
            target = event["targetPremium"]
            assert (target in present) if present else (target is None)
            expected_positions = sorted(
                (c, r) for c in range(reels) for r in range(rows)
                if board[c][r]["name"] in config.premium_symbols and board[c][r]["name"] != target
            )
            changes = event["positions"]
            positions = [(p["reel"], p["row"]) for p in changes]
            assert positions == expected_positions and event["convertedCount"] == len(positions)
            next_board = [[dict(s) for s in col] for col in board]
            for change in changes:
                c, r = change["reel"], change["row"]
                assert change["fromSymbol"] == board[c][r]["name"] and change["toSymbol"] == target
                next_board[c][r] = {"name": target}
            assert next_board == event["board"]
            board = next_board
            n3_used, n3_due = True, False
        elif kind == "multiplierZoneCreated":
            assert n4_due and not n4_used and not pending and not capped
            assert event["level"] == 4 and event["progression"] == progress
            assert event["cascade"] == cascades
            size = event["zoneSize"]
            assert size == config.n4_zone_size
            top_c, top_r = event["zoneTopLeft"]["reel"], event["zoneTopLeft"]["row"]
            assert 0 <= top_c <= reels - size and 0 <= top_r <= rows - size
            expected_zone = sorted((c, r) for c in range(top_c, top_c + size) for r in range(top_r, top_r + size))
            zone_entries = event["positions"]
            zone_positions = [(p["reel"], p["row"]) for p in zone_entries]
            assert zone_positions == expected_zone
            assert event["initialMultiplier"] == config.n4_multiplier_initial
            assert all(p["value"] == config.n4_multiplier_initial for p in zone_entries)
            wild_changes = event["wildPositions"]
            wild_positions = [(p["reel"], p["row"]) for p in wild_changes]
            eligible = sorted(pos for pos in expected_zone if pos not in sticky)
            assert len(wild_positions) == len(set(wild_positions)) == event["wildCount"]
            assert wild_positions == sorted(wild_positions)
            assert len(wild_positions) == min(config.n4_wild_count, len(eligible))
            assert set(wild_positions) <= set(eligible)
            assert set(wild_positions).isdisjoint(sticky), "N4 Wilds must never overwrite an N2 Wild position"
            next_board = [[dict(s) for s in col] for col in board]
            for change in wild_changes:
                c, r = change["reel"], change["row"]
                assert change["fromSymbol"] == board[c][r]["name"]
                next_board[c][r] = {"name": "WN4", "wild": True}
            assert next_board == event["board"]
            board = next_board
            n4_multipliers.update({pos: config.n4_multiplier_initial for pos in expected_zone})
            n4_used, n4_due = True, False
        elif kind == "multiplierUpgraded":
            # Unlike the other *_due activations, this fires between winInfo
            # (which just set `pending`) and the tumbleBoard that clears it,
            # so `pending` is expected to still be populated here.
            assert n4_upgrade_due and not capped
            assert event["level"] == 4 and event["progression"] == progress
            assert event["cascade"] == cascades
            got = [(c["reel"], c["row"], c["previousValue"], c["newValue"]) for c in event["positions"]]
            expected_changes = [(c["reel"], c["row"], c["previousValue"], c["newValue"])
                                 for c in expected_n4_changes]
            assert got == expected_changes and got
            for c in event["positions"]:
                pos = (c["reel"], c["row"])
                assert n4_multipliers[pos] == c["previousValue"]
                assert c["newValue"] == min(c["previousValue"] + 1, config.n4_multiplier_cap)
                assert c["newValue"] <= config.n4_multiplier_cap
                n4_multipliers[pos] = c["newValue"]
            expected_state = [{"reel": c, "row": r, "value": v} for (c, r), v in sorted(n4_multipliers.items())]
            assert event["multiplierState"] == expected_state
            n4_upgrade_due = False
        elif kind == "bossTriggered":
            assert n5_due and not n5_used and not pending and not capped
            assert event["level"] == 5 and event["progression"] == progress
            assert event["cascade"] == cascades
            size = event["size"]
            assert size == config.n5_boss_size
            top_c, top_r = event["anchor"]["reel"], event["anchor"]["row"]
            assert 0 <= top_c <= reels - size and 0 <= top_r <= rows - size
            expected_footprint = sorted(n5_footprint((top_c, top_r), size))
            footprint = [(p["reel"], p["row"]) for p in event["footprint"]]
            assert footprint == expected_footprint
            assert set(footprint).isdisjoint(sticky), "N5 boss must never overwrite an N2 Wild position"
            candidates = n5_candidate_placements(size, sticky, reels, rows)
            assert (top_c, top_r) in candidates
            min_overlap = n5_min_overlap_with_n4(size, candidates, set(n4_multipliers))
            actual_overlap = len(set(footprint) & set(n4_multipliers))
            assert actual_overlap == min_overlap, "initial 3x3 must avoid N4 whenever geometrically possible"
            next_board = [[dict(s) for s in col] for col in board]
            for c, r in footprint:
                next_board[c][r] = {"name": "WN5", "wild": True}
            assert next_board == event["board"]
            board = next_board
            n5_used, n5_due = True, False
            n5_active, n5_size, n5_anchor = True, size, (top_c, top_r)
            n5_positions = set(footprint)
            n5_awaiting_evaluation = True
            n5_just_triggered = True
        elif kind == "goldenFlyStarted":
            assert n5_just_triggered and n5_active and not capped and not golden_active
            assert event["level"] == 5 and event["progression"] == progress and event["cascade"] == cascades
            assert event["scope"] == "bossEvaluations"
            assert event["multiplier"] in {m for m, _ in config.golden_ladder}
            golden_active, golden_multiplier = True, event["multiplier"]
            n5_just_triggered = False
        elif kind in ("bossShrunk", "bossDefeated"):
            if n5_active and n5_awaiting_evaluation and not n5_relocate_due:
                # The size's dedicated evaluation paid nothing (no winInfo was
                # emitted): the boss still advances (V0.3.21).
                n5_awaiting_evaluation = False
                n5_relocate_due = True
                n5_expected_new_size = n5_size - 1
            assert n5_relocate_due and not capped
            assert event["level"] == 5 and event["progression"] == progress
            assert event["cascade"] == cascades
            assert event["previousSize"] == n5_size
            prev_anchor = (event["previousAnchor"]["reel"], event["previousAnchor"]["row"])
            assert prev_anchor == n5_anchor
            prev_footprint = [(p["reel"], p["row"]) for p in event["previousFootprint"]]
            assert prev_footprint == sorted(n5_positions)
            if kind == "bossDefeated":
                assert n5_expected_new_size == 0
                vacated = set(prev_footprint)
                entered = set()
                new_positions = set()
                new_size = new_anchor = None
            else:
                assert n5_expected_new_size == event["newSize"] and event["newSize"] > 0
                new_size = event["newSize"]
                new_anchor = (event["newAnchor"]["reel"], event["newAnchor"]["row"])
                assert new_anchor != prev_anchor, "the boss must move by at least one reel or row"
                new_footprint = [(p["reel"], p["row"]) for p in event["newFootprint"]]
                assert new_footprint == sorted(n5_footprint(new_anchor, new_size))
                new_positions = set(new_footprint)
                candidates = n5_candidate_placements(new_size, sticky, reels, rows)
                assert new_anchor in candidates and new_anchor != prev_anchor
                assert new_positions.isdisjoint(sticky), "N5 boss must never overwrite an N2 Wild position"
                vacated = set(prev_footprint) - new_positions
                entered = new_positions - set(prev_footprint)
            if kind == "bossShrunk":
                assert sorted({(p["reel"], p["row"]) for p in event["vacatedPositions"]}) == sorted(vacated)
                assert sorted({(p["reel"], p["row"]) for p in event["enteredPositions"]}) == sorted(entered)
            # self.n5_size/n5_anchor/n5_positions intentionally still describe
            # the *previous* footprint here (matching `board`, not yet
            # updated either): the transition is only applied once the
            # following tumbleBoard event has independently reconstructed the
            # post-move board, exactly mirroring how `board` itself is not
            # reassigned until that same tumbleBoard event. n5_next_* stash
            # the pending transition for that handler to apply.
            n5_board_expected = event["board"]
            n5_entered_positions = entered
            n5_next_defeated = kind == "bossDefeated"
            n5_next_size, n5_next_anchor, n5_next_positions = new_size, new_anchor, new_positions
            pending = vacated
            n5_tumble_pending = bool(pending)
            n5_relocate_due = False
        elif kind == "winInfo":
            assert not pending
            # Multiplier payouts must use the pre-upgrade snapshot: upgrades
            # from this very evaluation only take effect on later cascades.
            event_global = golden_multiplier if (golden_active and n5_active and n5_awaiting_evaluation) else 1
            expected = winning_clusters(board, config, n4_multipliers, event_global)
            actual = {(win["symbol"], frozenset((p["reel"], p["row"]) for p in win["positions"]),
                       win["win"], win["clusterMultiplier"]) for win in event["wins"]}
            assert all(win.get("globalMultiplier", 1) == event_global for win in event["wins"])
            assert actual == expected and actual
            assert len(actual) == len(event["wins"])
            assert all(win["clusterSize"] == len(win["positions"]) for win in event["wins"])
            assert event["totalWin"] == sum(win[2] for win in expected)
            total += event["totalWin"]
            all_win_positions = set().union(*(win[1] for win in expected))
            # Neither a sticky N2 Wild nor a current N5 boss cell is ever
            # actually removed by a win that touches it (see GAME_DESIGN.md):
            # `excluded` gates both explosion/removal and progression credit
            # for these. A WFLY Wild IS removed like any ordinary symbol
            # (so it must stay OUT of `excluded`, which feeds `pending` /
            # the tumbleBoard explosion check below) -- but its progression
            # credit alone is withheld by design (V0.3.17, see gamestate.py's
            # advance_progression), via the separate `progression_excluded`.
            excluded = sticky | n5_positions
            pending = all_win_positions - excluded
            fly_win_positions = {(c, r) for c, r in all_win_positions if board[c][r]["name"] == "WFLY"}
            progression_excluded = excluded | fly_win_positions
            expected_gain = rules.gain([
                {"symbol": sym, "clusterSize": len(positions), "win": win / 100,
                 "positions": [{"reel": c, "row": r} for c, r in positions - progression_excluded]}
                for sym, positions, win, _mult in expected
            ])
            # N4 multiplier upgrade: every multiplier position that took part
            # in a paid win (including one held by a sticky N2 Wild) steps up
            # by one, capped — computed here from the pre-upgrade snapshot so
            # the next event (if any) can be checked against it.
            expected_n4_changes = []
            for pos in sorted(all_win_positions & n4_multipliers.keys()):
                previous = n4_multipliers[pos]
                new = min(previous + 1, config.n4_multiplier_cap)
                if new != previous:
                    expected_n4_changes.append(
                        {"reel": pos[0], "row": pos[1], "previousValue": previous, "newValue": new})
            n4_upgrade_due = bool(expected_n4_changes)
            # N5 boss hit detection: only while this evaluation is still
            # "dedicated" to the boss's current size (see resolve_n5_boss_hit
            # in gamestate.py) — consumed here unconditionally, hit or miss.
            # Recorded only; promoted to the n5_relocate_due *guard* inside
            # the progressUpdate handler below, since gamestate.py's
            # activate_n5_boss() only runs after this win's own
            # tumbleBoard/progressUpdate/levelUp sequence has completed —
            # bossShrunk/bossDefeated is never the event immediately after
            # winInfo (or multiplierUpgraded).
            if n5_active and n5_awaiting_evaluation:
                n5_awaiting_evaluation = False
                n5_advance_this_evaluation = True
        elif kind == "tumbleBoard":
            assert pending and not capped
            expected_reason = "n5Boss" if n5_tumble_pending else None
            assert event.get("reason") == expected_reason
            removed = [(p["reel"], p["row"]) for p in event["explodingSymbols"]]
            assert set(removed) == pending and len(removed) == len(pending)
            # Protected exactly like a sticky N2 Wild: never a column barrier,
            # never mobile. An ordinary win-triggered tumble uses the still
            # currently-active footprint (n5_positions); an n5Boss-reason
            # tumble is gamestate.py's OWN vacate/refill step, which already
            # updates self.n5_boss_positions to the *new* footprint (staged
            # here as n5_next_positions, not yet promoted to n5_positions —
            # see the bossShrunk/bossDefeated handler above) before calling
            # tumble_board(), so only the truly vacated cells are mobile.
            fixed = sticky | (n5_next_positions if n5_tumble_pending else n5_positions)
            next_board = []
            for c in range(reels):
                mobile_rows = [r for r in range(rows) if (c, r) not in fixed]
                survivors = [board[c][r] for r in mobile_rows if (c, r) not in pending]
                assert len(event["newSymbols"][c]) == len(mobile_rows) - len(survivors)
                mobile = iter(event["newSymbols"][c] + survivors)
                next_board.append([board[c][r] if (c, r) in fixed else next(mobile) for r in range(rows)])
            if n5_tumble_pending:
                # gamestate.py stamps a fresh WN5 onto the entered positions
                # right after this same tumble, before either the bossShrunk/
                # bossDefeated event or this tumbleBoard event was emitted —
                # both already show the fully post-move board, so the
                # reconstruction must include it before the comparison below.
                for c, r in n5_entered_positions:
                    next_board[c][r] = {"name": "WN5", "wild": True}
            if sticky:
                assert event["stickyPositions"] == [{"reel": c, "row": r} for c, r in sorted(sticky)]
            assert next_board == event["board"]
            if n5_tumble_pending:
                assert next_board == n5_board_expected
                n5_board_expected = None
                n5_entered_positions = set()
                # Apply the transition stashed by the bossShrunk/bossDefeated
                # handler now that the post-move board has been verified.
                if n5_next_defeated:
                    n5_active, n5_defeated = False, True
                    n5_size, n5_anchor, n5_positions = 0, None, set()
                else:
                    n5_size, n5_anchor, n5_positions = n5_next_size, n5_next_anchor, n5_next_positions
                    n5_awaiting_evaluation = True
                n5_next_size = n5_next_anchor = n5_next_positions = None
                n5_next_defeated = False
            board = next_board
            pending = set()
            cascades += 1
            special_tumble = n5_tumble_pending
            update_reason = None if special_tumble else "symbolsRemoved"
            n5_tumble_pending = False
        elif kind == "wincap":
            assert total >= config.wincap * 100
            capped = True
        elif kind not in ("updateTumbleWin", "setWin", "setTotalWin", "finalWin"):
            raise AssertionError(f"Unexpected event: {kind}")
        if "amount" in event:
            assert event["amount"] == min(total, round(config.wincap * 100))
        assert len(board) == reels and all(len(column) == rows for column in board)
        assert all(s["name"] in (*config.symbols, "W", "WN2T", "WN4", "WN5", "WFLY")
                   for column in board for s in column)
        wfly_cells = {(c, r) for c in range(reels) for r in range(rows) if board[c][r]["name"] == "WFLY"}
        assert wfly_cells.isdisjoint(sticky), "a Fly temporary Wild must never become sticky"
        assert all(board[c][r] == {"name": "WFLY", "wild": True} for c, r in wfly_cells)
        wn2t_cells = {(c, r) for c in range(reels) for r in range(rows) if board[c][r]["name"] == "WN2T"}
        assert wn2t_cells.isdisjoint(sticky), "an N2 temporary Wild must never become sticky"
        assert all(board[c][r] == {"name": "WN2T", "wild": True} for c, r in wn2t_cells)
        assert {(c, r) for c in range(reels) for r in range(rows) if board[c][r]["name"] == "W"} == sticky
        assert all(board[c][r] == {"name": "W", "wild": True, "sticky": True} for c, r in sticky)
        wn4_cells = {(c, r) for c in range(reels) for r in range(rows) if board[c][r]["name"] == "WN4"}
        assert wn4_cells.isdisjoint(sticky), "an N4 temporary Wild must never become sticky"
        assert all(board[c][r] == {"name": "WN4", "wild": True} for c, r in wn4_cells)
        assert all(0 < v <= config.n4_multiplier_cap for v in n4_multipliers.values())
        wn5_cells = {(c, r) for c in range(reels) for r in range(rows) if board[c][r]["name"] == "WN5"}
        assert wn5_cells == n5_positions, "the WN5 board footprint must always match the tracked N5 positions"
        assert wn5_cells.isdisjoint(sticky), "the N5 boss must never occupy an N2 Wild position"
        assert all(board[c][r] == {"name": "WN5", "wild": True} for c, r in wn5_cells)
    assert capped or (not pending and not winning_clusters(board, config))
    assert start_seen and update_reason is None and not expected_levels
    assert not fly_pending, "flyMiniBonusTriggered must always appear when config.fly_minibonus_enabled"
    # Every *_due/pending flag below marks a consequence of the round's very
    # last winInfo (a new level crossed, a boss hit registered, ...) that is
    # normally applied by the next tumbleBoard/progressUpdate/feature-event
    # sequence -- but every one of those events is itself unconditionally
    # gated on "and not capped" earlier in this function (see wildAdded,
    # gridTransform, multiplierZoneCreated, multiplierUpgraded, bossShrunk/
    # bossDefeated above): once a round's cumulative win reaches the wincap,
    # gamestate.py's run_spin() stops immediately (its own cascade loop
    # condition is "while totalWin > 0 and not wincap_triggered") and never
    # applies the triggering win's own consequences, exactly like every
    # other win already relies on a *following* loop iteration to be
    # credited/acted upon. A capped round is therefore expected to end with
    # some of these flags still outstanding; demanding they be resolved
    # regardless of `capped` would contradict the very guards above that
    # forbid resolving them once capped. Found and fixed in V0.3.13 when a
    # rare capping win that also hit the N5 boss first exposed the
    # inconsistency (see docs/milestones/V0.3.13_paytable_7x7_rtp96.md).
    assert capped or (not n2_due and not n3_due)
    assert capped or (not n4_due and not n4_upgrade_due)
    assert capped or (not n5_due and not n5_relocate_due and not n5_tumble_pending and n5_board_expected is None)
    assert capped or not n5_advance_this_evaluation, \
        "a completed N5 evaluation must always be promoted by the next progressUpdate unless the round capped first"
    assert capped or not n5_active, "N5 must always complete (3x3, 2x2, 1x1, defeated): no stuck Mega Wild"
    assert capped or (n5_next_size is None and n5_next_anchor is None and n5_next_positions is None)
    assert capped or not n5_next_defeated
    assert book["payoutMultiplier"] == min(total, round(config.wincap * 100))
    assert book["freeGameWins"] == 0
    assert round(book["baseGameWins"] * 100) == book["payoutMultiplier"]
    assert cascades <= config.max_cascades
    return cascades, capped


def validate_library(config, expected_count, lookup_path=None):
    """Replay every book. `lookup_path` defaults to the published
    lookUpTable_base_0.csv, which must be unit-weight here (development runs).
    publish.py passes the unit-weight lookup_tables/lookUpTable_base.csv
    instead, because its published table is reweighted (see publication.py)."""
    publish = Path(config.publish_path)
    with Path(lookup_path or publish / "lookUpTable_base_0.csv").open() as stream:
        rows = [tuple(map(int, row)) for row in csv.reader(stream)]
    assert len(rows) == expected_count
    assert len({row[0] for row in rows}) == expected_count
    assert all(weight == 1 and payout >= 0 for _, weight, payout in rows)
    count = hits = cap_hits = total_cascades = max_cascades = 0
    total_progress = 0
    fly_rounds = fly_wilds_created = fly_wilds_consumed = fly_win_sum = 0
    fly_immediate_win_rounds = 0
    n2_rounds = n2_wilds = 0
    n2_win_sum = n2_wild_wins = n2_wild_participations = n2_wins_after_activation = 0
    n3_rounds = n3_converted_total = n3_win_sum = n3_immediate_win_rounds = 0
    n3_premium_count_dist = {}
    n3_target_dist = {}
    n4_rounds = n4_win_sum = n4_immediate_win_rounds = 0
    n4_wilds_created = n4_wilds_consumed = n4_wilds_never_consumed = 0
    n4_upgrade_events_total = n4_positions_upgraded_total = 0
    n4_cascades_before_total = n4_cascades_after_total = 0
    n4_zone_top_left_dist = {}
    n4_max_multiplier_dist = {}  # highest value any position reached, per activated round
    n4_reached_x3 = n4_reached_x4 = n4_reached_x5 = 0
    n5_rounds = n5_win_sum = n5_defeat_win_sum = n5_max_win = 0
    n5_initial_placement_dist = {}
    n5_hit_count_dist = {}
    n5_hits_total = n5_reached_2x2 = n5_reached_1x1 = n5_defeated_rounds = 0
    n5_2x2_overlaps_n4 = n5_1x1_overlaps_n4 = 0
    n5_cascades_before_total = n5_cascades_after_total = 0
    n5_boss_assisted_clusters = 0
    n5_boss_assisted_multiplier_sum = n5_boss_assisted_multiplier_count = 0
    n5_boss_assisted_max_multiplier = 0
    level_counts = {str(level): 0 for level in range(2, len(config.progression_rules.stages) + 1)}
    # Mutually-exclusive RTP decomposition by the *highest* level a round
    # reached (1-5): every round's payoutMultiplier is attributed to exactly
    # one bucket (its own highest activation), so these sum to the overall
    # RTP -- unlike the *_win_sum values above, which are per-feature and
    # overlap (a round that reaches N5 also counts in n2/n3/n4's own sums).
    highest_level_win_sum = {level: 0 for level in range(1, 6)}
    highest_level_round_count = {level: 0 for level in range(1, 6)}
    # DEV/PROVISIONAL cluster-size buckets, matching the piste in GAME_DESIGN.md;
    # not a validated paytable tranche.
    cluster_buckets = {"5-6": 0, "7-9": 0, "10-12": 0, "13-15": 0, "16-18": 0, "19-21+": 0}
    total_clusters = 0
    example = None
    with (publish / "books_base.jsonl.zst").open("rb") as stream:
        with zstandard.ZstdDecompressor().stream_reader(stream) as reader:
            for line in io.TextIOWrapper(reader):
                book = json.loads(line)
                assert (book["id"], 1, book["payoutMultiplier"]) == rows[count]
                cascades, capped = validate_book(book, config)
                for win_event in (e for e in book["events"] if e["type"] == "winInfo"):
                    for win in win_event["wins"]:
                        total_clusters += 1
                        size = win["clusterSize"]
                        if size <= 6:
                            cluster_buckets["5-6"] += 1
                        elif size <= 9:
                            cluster_buckets["7-9"] += 1
                        elif size <= 12:
                            cluster_buckets["10-12"] += 1
                        elif size <= 15:
                            cluster_buckets["13-15"] += 1
                        elif size <= 18:
                            cluster_buckets["16-18"] += 1
                        else:
                            cluster_buckets["19-21+"] += 1
                fly_events = [e for e in book["events"] if e["type"] == "flyMiniBonusTriggered"]
                if fly_events and fly_events[0]["triggered"]:
                    activation = fly_events[0]
                    fly_rounds += 1
                    fly_win_sum += book["payoutMultiplier"]
                    wild_positions = {(p["reel"], p["row"]) for p in activation["positions"]}
                    fly_wilds_created += len(wild_positions)
                    following = book["events"][activation["index"] + 1:activation["index"] + 2]
                    if following and following[0]["type"] == "progressUpdate":
                        following = book["events"][activation["index"] + 2:activation["index"] + 3]
                    if following and following[0]["type"] == "winInfo":
                        fly_immediate_win_rounds += 1
                    remaining = set(wild_positions)
                    for e in book["events"][activation["index"] + 1:]:
                        if e["type"] == "tumbleBoard":
                            removed = {(p["reel"], p["row"]) for p in e["explodingSymbols"]}
                            remaining -= removed
                    fly_wilds_consumed += len(wild_positions) - len(remaining)
                n2_wilds_events = [e for e in book["events"] if e["type"] == "wildAdded"]
                n2_rounds += len(n2_wilds_events)
                n2_wilds += sum(e["wildCount"] for e in n2_wilds_events)
                if n2_wilds_events:
                    n2_win_sum += book["payoutMultiplier"]
                    activation = n2_wilds_events[-1]
                    sticky_after_n2 = {(p["reel"], p["row"]) for p in activation["stickyPositions"]}
                    for win_event in book["events"][activation["index"] + 1:]:
                        if win_event["type"] != "winInfo":
                            continue
                        for win in win_event["wins"]:
                            overlap = sum(1 for p in win["positions"]
                                          if (p["reel"], p["row"]) in sticky_after_n2)
                            if overlap:
                                n2_wild_wins += 1
                                n2_wild_participations += overlap
                            n2_wins_after_activation += 1
                n3_events = [e for e in book["events"] if e["type"] == "gridTransform"]
                n3_rounds += len(n3_events)
                if n3_events:
                    activation = n3_events[0]
                    n3_win_sum += book["payoutMultiplier"]
                    n3_converted_total += activation["convertedCount"]
                    premium_count = len(activation["premiumTypesPresent"])
                    n3_premium_count_dist[premium_count] = n3_premium_count_dist.get(premium_count, 0) + 1
                    if activation["targetPremium"] is not None:
                        target = activation["targetPremium"]
                        n3_target_dist[target] = n3_target_dist.get(target, 0) + 1
                    following = book["events"][activation["index"] + 1:activation["index"] + 2]
                    if following and following[0]["type"] == "winInfo":
                        n3_immediate_win_rounds += 1
                n4_events = [e for e in book["events"] if e["type"] == "multiplierZoneCreated"]
                n4_rounds += len(n4_events)
                if n4_events:
                    activation = n4_events[0]
                    n4_win_sum += book["payoutMultiplier"]
                    top = (activation["zoneTopLeft"]["reel"], activation["zoneTopLeft"]["row"])
                    key = f"{top[0]},{top[1]}"
                    n4_zone_top_left_dist[key] = n4_zone_top_left_dist.get(key, 0) + 1
                    wild_positions = {(p["reel"], p["row"]) for p in activation["wildPositions"]}
                    n4_wilds_created += len(wild_positions)
                    following = book["events"][activation["index"] + 1:activation["index"] + 2]
                    if following and following[0]["type"] == "winInfo":
                        n4_immediate_win_rounds += 1
                    remaining = set(wild_positions)
                    cascades_before = sum(1 for e in book["events"][:activation["index"]]
                                           if e["type"] == "tumbleBoard")
                    n4_cascades_before_total += cascades_before
                    n4_cascades_after_total += cascades - cascades_before
                    max_value_by_position = {}
                    for e in book["events"][activation["index"] + 1:]:
                        if e["type"] == "tumbleBoard":
                            removed = {(p["reel"], p["row"]) for p in e["explodingSymbols"]}
                            remaining -= removed
                        elif e["type"] == "multiplierUpgraded":
                            n4_upgrade_events_total += 1
                            for c in e["positions"]:
                                pos = (c["reel"], c["row"])
                                n4_positions_upgraded_total += 1
                                max_value_by_position[pos] = max(max_value_by_position.get(pos, 2), c["newValue"])
                    n4_wilds_consumed += len(wild_positions) - len(remaining)
                    n4_wilds_never_consumed += len(remaining)
                    highest = max(max_value_by_position.values(), default=2)
                    n4_max_multiplier_dist[highest] = n4_max_multiplier_dist.get(highest, 0) + 1
                    n4_reached_x3 += any(v >= 3 for v in max_value_by_position.values())
                    n4_reached_x4 += any(v >= 4 for v in max_value_by_position.values())
                    n4_reached_x5 += any(v >= 5 for v in max_value_by_position.values())
                n5_trigger = [e for e in book["events"] if e["type"] == "bossTriggered"]
                n5_rounds += len(n5_trigger)
                if n5_trigger:
                    trigger = n5_trigger[0]
                    n5_win_sum += book["payoutMultiplier"]
                    n5_max_win = max(n5_max_win, book["payoutMultiplier"])
                    top = (trigger["anchor"]["reel"], trigger["anchor"]["row"])
                    n5_initial_placement_dist[f"{top[0]},{top[1]}"] = (
                        n5_initial_placement_dist.get(f"{top[0]},{top[1]}", 0) + 1)
                    shrinks = [e for e in book["events"] if e["type"] == "bossShrunk"]
                    defeats = [e for e in book["events"] if e["type"] == "bossDefeated"]
                    hit_count = len(shrinks) + len(defeats)
                    n5_hits_total += hit_count
                    n5_hit_count_dist[hit_count] = n5_hit_count_dist.get(hit_count, 0) + 1
                    reached_2x2 = len(shrinks) >= 1  # a hit at 3x3
                    reached_1x1 = len(shrinks) >= 2  # a hit at 2x2
                    defeated = bool(defeats)  # a hit at 1x1
                    n5_reached_2x2 += reached_2x2
                    n5_reached_1x1 += reached_1x1
                    n5_defeated_rounds += defeated
                    if defeated:
                        n5_defeat_win_sum += book["payoutMultiplier"]
                    n4_zone_events = [e for e in book["events"] if e["type"] == "multiplierZoneCreated"]
                    n4_zone = ({(p["reel"], p["row"]) for p in n4_zone_events[0]["positions"]}
                               if n4_zone_events else set())
                    if reached_2x2 and n4_zone:
                        new_2x2 = {(p["reel"], p["row"]) for p in shrinks[0]["newFootprint"]}
                        n5_2x2_overlaps_n4 += bool(new_2x2 & n4_zone)
                    if reached_1x1 and n4_zone:
                        new_1x1 = {(p["reel"], p["row"]) for p in shrinks[1]["newFootprint"]}
                        n5_1x1_overlaps_n4 += bool(new_1x1 & n4_zone)
                    cascades_before = sum(1 for e in book["events"][:trigger["index"]]
                                           if e["type"] == "tumbleBoard")
                    n5_cascades_before_total += cascades_before
                    n5_cascades_after_total += cascades - cascades_before
                    footprint = {(p["reel"], p["row"]) for p in trigger["footprint"]}
                    for e in book["events"][trigger["index"] + 1:]:
                        if e["type"] == "bossShrunk":
                            footprint = {(p["reel"], p["row"]) for p in e["newFootprint"]}
                        elif e["type"] == "winInfo":
                            for win in e["wins"]:
                                win_positions = {(p["reel"], p["row"]) for p in win["positions"]}
                                if win_positions & footprint:
                                    n5_boss_assisted_clusters += 1
                                    mult = win["clusterMultiplier"]
                                    n5_boss_assisted_multiplier_sum += mult
                                    n5_boss_assisted_multiplier_count += 1
                                    n5_boss_assisted_max_multiplier = max(n5_boss_assisted_max_multiplier, mult)
                if n5_trigger:
                    highest_level = 5
                elif n4_events:
                    highest_level = 4
                elif n3_events:
                    highest_level = 3
                elif n2_wilds_events:
                    highest_level = 2
                else:
                    highest_level = 1
                highest_level_win_sum[highest_level] += book["payoutMultiplier"]
                highest_level_round_count[highest_level] += 1
                final_progress = [e for e in book["events"] if e["type"] == "progressUpdate"][-1]
                total_progress += final_progress["newProgress"]
                for level in level_counts:
                    level_counts[level] += final_progress["newLevel"] >= int(level)
                hits += book["payoutMultiplier"] > 0
                cap_hits += capped
                total_cascades += cascades
                max_cascades = max(max_cascades, cascades)
                if example is None or cascades > example_cascades:
                    example = book
                    example_cascades = cascades
                count += 1
    assert count == expected_count
    if example is not None:
        Path(config.library_path, "example_round.json").write_text(
            json.dumps(example, indent=2) + "\n", encoding="utf-8"
        )
    return {
        "books_verified": count, "winning_rounds": hits,
        "observed_hit_rate": hits / count,
        "observed_rtp_unoptimized": sum(row[2] for row in rows) / (100 * count),
        "configured_rtp_placeholder": config.rtp,
        "max_observed_win": max(row[2] for row in rows) / 100,
        "mean_win_per_round": sum(row[2] for row in rows) / (100 * count),
        "mean_win_per_winning_round": (sum(row[2] for row in rows) / (100 * hits)) if hits else 0,
        "total_clusters": total_clusters,
        "cluster_size_bucket_counts": cluster_buckets,
        "total_cascades": total_cascades, "max_cascades": max_cascades,
        "cap_hits": cap_hits, "example_round_id": None if example is None else example["id"],
        "mean_cascades": total_cascades / count,
        "mean_progression": total_progress / count,
        "level_reached_counts": level_counts,
        "level_reached_percent": {level: reached * 100 / count for level, reached in level_counts.items()},
        "progression_thresholds": list(config.progression_rules.thresholds),
        "fly_minibonus_enabled": config.fly_minibonus_enabled,
        "fly_trigger_probability_configured": config.fly_trigger_probability,
        "fly_activations": fly_rounds,
        "fly_activation_rate_percent": fly_rounds * 100 / count,
        "books_per_fly_activation": count / fly_rounds if fly_rounds else None,
        "fly_wilds_created": fly_wilds_created,
        "mean_fly_wilds_per_activation": fly_wilds_created / fly_rounds if fly_rounds else 0,
        "fly_wilds_consumed": fly_wilds_consumed,
        "fly_wild_consumption_rate_percent": (
            fly_wilds_consumed * 100 / fly_wilds_created if fly_wilds_created else 0
        ),
        "mean_win_conditional_on_fly": (fly_win_sum / (100 * fly_rounds)) if fly_rounds else 0,
        # Overlapping ("at least this level") RTP share, mirroring n5's own
        # long-standing field below -- NOT mutually exclusive across levels,
        # see highest_level_rtp_contribution_percent near the end of this
        # dict for a partition that sums to the overall RTP.
        "fly_contribution_to_dev_rtp_percent": (fly_win_sum / (100 * count)) if count else 0,
        "fly_immediate_win_activations": fly_immediate_win_rounds,
        "fly_immediate_win_rate_percent": (
            fly_immediate_win_rounds * 100 / fly_rounds if fly_rounds else 0
        ),
        "n2_activations": n2_rounds,
        "n2_frequency_percent": n2_rounds * 100 / count,
        "n2_wilds_created": n2_wilds,
        "mean_n2_wilds_per_activation": n2_wilds / n2_rounds if n2_rounds else 0,
        "mean_n2_wilds_per_round": n2_wilds / count,
        "n2_configured_wild_count": config.n2_wild_count,
        "mean_win_conditional_on_n2": (n2_win_sum / (100 * n2_rounds)) if n2_rounds else 0,
        "n2_wild_participating_win_count": n2_wild_wins,
        "n2_wins_after_activation": n2_wins_after_activation,
        "n2_wild_participation_rate_percent": (
            n2_wild_wins * 100 / n2_wins_after_activation if n2_wins_after_activation else 0
        ),
        "mean_wild_positions_per_participating_win": (
            n2_wild_participations / n2_wild_wins if n2_wild_wins else 0
        ),
        "n2_contribution_to_dev_rtp_percent": (n2_win_sum / (100 * count)) if count else 0,
        "n3_activations": n3_rounds,
        "n3_frequency_percent": n3_rounds * 100 / count,
        "n3_premium_types_present_distribution": n3_premium_count_dist,
        "n3_target_premium_distribution": n3_target_dist,
        "n3_total_symbols_converted": n3_converted_total,
        "mean_n3_symbols_converted_per_activation": (
            n3_converted_total / n3_rounds if n3_rounds else 0
        ),
        "mean_win_conditional_on_n3": (n3_win_sum / (100 * n3_rounds)) if n3_rounds else 0,
        "n3_immediate_win_activations": n3_immediate_win_rounds,
        "n3_immediate_win_rate_percent": (
            n3_immediate_win_rounds * 100 / n3_rounds if n3_rounds else 0
        ),
        "n3_contribution_to_dev_rtp_percent": (n3_win_sum / (100 * count)) if count else 0,
        "n4_activations": n4_rounds,
        "n4_frequency_percent": n4_rounds * 100 / count,
        "n4_zone_top_left_distribution": n4_zone_top_left_dist,
        "n4_configured_wild_count": config.n4_wild_count,
        "n4_wilds_created": n4_wilds_created,
        "mean_n4_wilds_per_activation": n4_wilds_created / n4_rounds if n4_rounds else 0,
        "mean_win_conditional_on_n4": (n4_win_sum / (100 * n4_rounds)) if n4_rounds else 0,
        "n4_immediate_win_activations": n4_immediate_win_rounds,
        "n4_immediate_win_rate_percent": (
            n4_immediate_win_rounds * 100 / n4_rounds if n4_rounds else 0
        ),
        "n4_wilds_consumed": n4_wilds_consumed,
        "n4_wilds_never_consumed": n4_wilds_never_consumed,
        "n4_wild_never_consumed_percent": (
            n4_wilds_never_consumed * 100 / n4_wilds_created if n4_wilds_created else 0
        ),
        "n4_upgrade_events_total": n4_upgrade_events_total,
        "n4_positions_upgraded_total": n4_positions_upgraded_total,
        "mean_n4_positions_upgraded_per_activation": (
            n4_positions_upgraded_total / n4_rounds if n4_rounds else 0
        ),
        "n4_max_multiplier_value_distribution": n4_max_multiplier_dist,
        "n4_reached_x3_activations": n4_reached_x3,
        "n4_reached_x3_rate_percent": (n4_reached_x3 * 100 / n4_rounds if n4_rounds else 0),
        "n4_reached_x4_activations": n4_reached_x4,
        "n4_reached_x4_rate_percent": (n4_reached_x4 * 100 / n4_rounds if n4_rounds else 0),
        "n4_reached_x5_activations": n4_reached_x5,
        "n4_reached_x5_rate_percent": (n4_reached_x5 * 100 / n4_rounds if n4_rounds else 0),
        "n4_mean_cascades_before_activation": (n4_cascades_before_total / n4_rounds if n4_rounds else 0),
        "n4_mean_cascades_after_activation": (n4_cascades_after_total / n4_rounds if n4_rounds else 0),
        "n4_contribution_to_dev_rtp_percent": (n4_win_sum / (100 * count)) if count else 0,
        "n5_activations": n5_rounds,
        "n5_frequency_percent": n5_rounds * 100 / count,
        "n5_initial_placement_distribution": n5_initial_placement_dist,
        "mean_win_conditional_on_n5": (n5_win_sum / (100 * n5_rounds)) if n5_rounds else 0,
        "n5_max_win_conditional_on_n5": n5_max_win / 100,
        "n5_contribution_to_dev_rtp_percent": (n5_win_sum / (100 * count)) if count else 0,
        # Boss-stage funnel: 3x3 hit rate == reached-2x2 rate (a hit at 3x3 is
        # exactly what causes the shrink to 2x2), and so on down the chain.
        "n5_3x3_hit_rate_percent": (n5_reached_2x2 * 100 / n5_rounds if n5_rounds else 0),
        "n5_reached_2x2_activations": n5_reached_2x2,
        "n5_reached_2x2_rate_percent": (n5_reached_2x2 * 100 / n5_rounds if n5_rounds else 0),
        "n5_2x2_hit_rate_percent": (n5_reached_1x1 * 100 / n5_reached_2x2 if n5_reached_2x2 else 0),
        "n5_reached_1x1_activations": n5_reached_1x1,
        "n5_reached_1x1_rate_percent": (n5_reached_1x1 * 100 / n5_rounds if n5_rounds else 0),
        "n5_1x1_hit_rate_percent": (n5_defeated_rounds * 100 / n5_reached_1x1 if n5_reached_1x1 else 0),
        "n5_defeated_activations": n5_defeated_rounds,
        "n5_defeat_rate_per_activation_percent": (n5_defeated_rounds * 100 / n5_rounds if n5_rounds else 0),
        "n5_defeat_rate_per_book_percent": (n5_defeated_rounds * 100 / count if count else 0),
        "n5_stuck_at_3x3_activations": n5_rounds - n5_reached_2x2,
        "n5_stuck_at_2x2_activations": n5_reached_2x2 - n5_reached_1x1,
        "n5_stuck_at_1x1_activations": n5_reached_1x1 - n5_defeated_rounds,
        "n5_hits_total": n5_hits_total,
        "mean_hits_per_n5_activation": (n5_hits_total / n5_rounds if n5_rounds else 0),
        "n5_hit_count_distribution": n5_hit_count_dist,
        "mean_win_conditional_on_n5_defeat": (n5_defeat_win_sum / (100 * n5_defeated_rounds)
                                               if n5_defeated_rounds else 0),
        "n5_2x2_overlap_with_n4_activations": n5_2x2_overlaps_n4,
        "n5_2x2_overlap_with_n4_rate_percent": (
            n5_2x2_overlaps_n4 * 100 / n5_reached_2x2 if n5_reached_2x2 else 0),
        "n5_1x1_overlap_with_n4_activations": n5_1x1_overlaps_n4,
        "n5_1x1_overlap_with_n4_rate_percent": (
            n5_1x1_overlaps_n4 * 100 / n5_reached_1x1 if n5_reached_1x1 else 0),
        "n5_boss_assisted_clusters": n5_boss_assisted_clusters,
        "mean_boss_assisted_clusters_per_activation": (
            n5_boss_assisted_clusters / n5_rounds if n5_rounds else 0),
        "n5_boss_assisted_mean_additive_n4_multiplier": (
            n5_boss_assisted_multiplier_sum / n5_boss_assisted_multiplier_count
            if n5_boss_assisted_multiplier_count else 0),
        "n5_boss_assisted_max_additive_n4_multiplier": n5_boss_assisted_max_multiplier,
        "n5_mean_cascades_before_activation": (n5_cascades_before_total / n5_rounds if n5_rounds else 0),
        "n5_mean_cascades_after_activation": (n5_cascades_after_total / n5_rounds if n5_rounds else 0),
        # Mutually-exclusive RTP decomposition by the highest level reached
        # per round (see highest_level_win_sum above): these five values sum
        # to observed_rtp_unoptimized, unlike the overlapping *_contribution_
        # to_dev_rtp_percent fields above (n2/n3/n4/n5/fly), which double-count
        # a round that reached e.g. N5 under every lower level's own sum too.
        "highest_level_round_counts": {str(k): v for k, v in highest_level_round_count.items()},
        "highest_level_rtp_contribution_percent": {
            str(level): (highest_level_win_sum[level] / (100 * count)) if count else 0
            for level in range(1, 6)
        },
    }
