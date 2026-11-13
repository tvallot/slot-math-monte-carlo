"""Zero-based active-grid coordinates; all event wins are hundredths of a bet."""

from src.events.events import tumble_board_event, json_ready_sym


def board_snapshot(state):
    return [[json_ready_sym(symbol, list(state.config.special_symbols)) for symbol in column]
            for column in state.board]


def sticky_positions(state):
    return [{"reel": c, "row": r} for c, r in sorted(state.sticky_positions)]


def cluster_win_event(state, global_multiplier=1):
    # Serialize explicitly to keep integer rounding identical for all win fields.
    # clusterMultiplier is the SDK's own additive sum of N4 multiplier-position
    # values across this cluster (floored at 1 when none participate); exposed
    # here so replay/frontend never have to recompute it independently.
    # globalMultiplier (Golden Mega Fly, V0.3.21) is added to each win ONLY
    # when it is not 1, so every non-Golden book stays byte-identical.
    state.book.add_event({
        "index": len(state.book.events),
        "type": "winInfo",
        "totalWin": int(round(state.win_data["totalWin"] * 100)),
        "wins": [
            {"symbol": win["symbol"], "clusterSize": win["clusterSize"],
             "positions": win["positions"], "win": int(round(win["win"] * 100)),
             "clusterMultiplier": win["meta"]["clusterMult"],
             **({"globalMultiplier": global_multiplier} if global_multiplier != 1 else {})}
            for win in state.win_data["wins"]
        ],
    })


def cascade_event(state, n5_positions=None):
    tumble_board_event(state)
    reason, positions = None, None
    if n5_positions is not None:
        reason, positions = "n5Boss", n5_positions
    state.book.append_book_items(len(state.book.events) - 1, {
        "board": board_snapshot(state),
        **({"reason": reason, "explodingSymbols": positions} if reason is not None else {}),
    })
    event = state.book.events[-1]
    # A Wild (N2 persistent or N4 temporary) may be shared by several
    # clusters within one board evaluation, so the SDK's own
    # tumble_board_event() can list the same position more than once here
    # (once per win it belongs to) even with no sticky Wild on the board at
    # all; always deduplicate. Sticky (N2) positions are never physically
    # removed, so they are additionally dropped from the reported explosion.
    # The current N5 Mega Wild footprint is protected the same way: the SDK's
    # tumble_board_event() builds explodingSymbols straight from win_data's
    # win positions, not from actual removal, so a boss cell that merely
    # participated in a win (its own explode flag was already forced back to
    # False in evaluate_board) would otherwise be misreported as removed.
    removed = ({(p["reel"], p["row"]) for p in event["explodingSymbols"]}
               - state.sticky_positions - state.n5_boss_positions)
    update = {"explodingSymbols": [{"reel": c, "row": r} for c, r in sorted(removed)]}
    if state.sticky_positions:
        update["stickyPositions"] = sticky_positions(state)
    state.book.append_book_items(len(state.book.events) - 1, update)


def wild_added_event(state, positions, temporary_positions=None):
    state.book.add_event({
        "index": len(state.book.events), "type": "wildAdded",
        "level": 2, "progression": state.progression, "cascade": state.cascade_count,
        "positions": positions, "wildCount": len(positions),
        "stickyPositions": sticky_positions(state), "board": board_snapshot(state),
        # Only present when the opt-in N2 "mixed Wilds" variant places
        # temporary WN2T Wilds (config.n2_temp_wild_count > 0), so the event
        # stays byte-identical to every pre-existing caller otherwise.
        **({"temporaryPositions": temporary_positions,
            "temporaryWildCount": len(temporary_positions)} if temporary_positions else {}),
    })


def grid_transform_event(state, premium_types_present, target_premium, positions):
    state.book.add_event({
        "index": len(state.book.events), "type": "gridTransform",
        "level": 3, "progression": state.progression, "cascade": state.cascade_count,
        "premiumTypesPresent": premium_types_present, "targetPremium": target_premium,
        "positions": positions, "convertedCount": len(positions),
        "board": board_snapshot(state),
    })


def multiplier_zone_created_event(state, top_reel, top_row, zone_positions, initial_value, wild_positions):
    state.book.add_event({
        "index": len(state.book.events), "type": "multiplierZoneCreated",
        "level": 4, "progression": state.progression, "cascade": state.cascade_count,
        "zoneTopLeft": {"reel": top_reel, "row": top_row}, "zoneSize": state.config.n4_zone_size,
        "positions": [{"reel": c, "row": r, "value": initial_value} for c, r in zone_positions],
        "initialMultiplier": initial_value,
        "wildPositions": wild_positions, "wildCount": len(wild_positions),
        "board": board_snapshot(state),
    })


def multiplier_upgraded_event(state, changes):
    state.book.add_event({
        "index": len(state.book.events), "type": "multiplierUpgraded",
        "level": 4, "progression": state.progression, "cascade": state.cascade_count,
        "positions": changes,
        "multiplierState": [{"reel": c, "row": r, "value": v}
                             for (c, r), v in sorted(state.n4_multipliers.items())],
    })


def boss_triggered_event(state, top_reel, top_row, size, footprint):
    state.book.add_event({
        "index": len(state.book.events), "type": "bossTriggered",
        "level": 5, "progression": state.progression, "cascade": state.cascade_count,
        "size": size, "anchor": {"reel": top_reel, "row": top_row},
        "footprint": [{"reel": c, "row": r} for c, r in footprint],
        "board": board_snapshot(state),
    })


def boss_shrunk_event(state, previous_size, new_size, previous_anchor, new_anchor,
                       previous_footprint, new_footprint, vacated_positions, entered_positions):
    state.book.add_event({
        "index": len(state.book.events), "type": "bossShrunk",
        "level": 5, "progression": state.progression, "cascade": state.cascade_count,
        "previousSize": previous_size, "newSize": new_size,
        "previousAnchor": {"reel": previous_anchor[0], "row": previous_anchor[1]},
        "newAnchor": {"reel": new_anchor[0], "row": new_anchor[1]},
        "previousFootprint": [{"reel": c, "row": r} for c, r in previous_footprint],
        "newFootprint": [{"reel": c, "row": r} for c, r in new_footprint],
        "vacatedPositions": [{"reel": c, "row": r} for c, r in vacated_positions],
        "enteredPositions": [{"reel": c, "row": r} for c, r in entered_positions],
        "board": board_snapshot(state),
    })


def boss_defeated_event(state, previous_size, previous_anchor, previous_footprint):
    state.book.add_event({
        "index": len(state.book.events), "type": "bossDefeated",
        "level": 5, "progression": state.progression, "cascade": state.cascade_count,
        "previousSize": previous_size,
        "previousAnchor": {"reel": previous_anchor[0], "row": previous_anchor[1]},
        "previousFootprint": [{"reel": c, "row": r} for c, r in previous_footprint],
        "board": board_snapshot(state),
    })


def golden_fly_started_event(state, multiplier):
    # Emitted once, right after bossTriggered, only in a Golden round.
    # `multiplier` is applied (as winInfo globalMultiplier) to the single
    # dedicated evaluation of each boss size and to nothing else.
    state.book.add_event({
        "index": len(state.book.events), "type": "goldenFlyStarted",
        "level": 5, "progression": state.progression, "cascade": state.cascade_count,
        "multiplier": multiplier, "scope": "bossEvaluations",
    })


def fly_minibonus_triggered_event(state, triggered, wild_count, positions):
    # Always emitted once per round when config.fly_minibonus_enabled, even
    # when the roll missed (triggered=False, wildCount=0, positions=[]), so
    # replay/frontend never have to infer the outcome from absence of an
    # event. Emitted right after `reveal`, before the roundStart
    # progressUpdate — level/progression/cascade are always 0/0/0 here by
    # construction (see gamestate.py, activate_fly_minibonus).
    state.book.add_event({
        "index": len(state.book.events), "type": "flyMiniBonusTriggered",
        "level": 1, "progression": state.progression, "cascade": state.cascade_count,
        "triggered": triggered, "wildCount": wild_count,
        "positions": positions, "board": board_snapshot(state),
    })


def progress_update_event(state, previous_progress, previous_level, reason):
    rules = state.config.progression_rules
    state.book.add_event({
        "index": len(state.book.events), "type": "progressUpdate",
        "reason": reason, "cascade": state.cascade_count,
        "previousProgress": previous_progress, "newProgress": state.progression,
        "delta": state.progression - previous_progress,
        "previousLevel": previous_level, "newLevel": state.level,
        "thresholdsReached": list(rules.thresholds[previous_level - 1:state.level - 1]),
        "nextThreshold": rules.thresholds[state.level - 1] if state.level <= len(rules.thresholds) else None,
        **({"levels": rules.levels()} if reason == "roundStart" else {}),
    })


def level_up_event(state, previous_progress, level):
    rules = state.config.progression_rules
    state.book.add_event({
        "index": len(state.book.events), "type": "levelUp",
        "cascade": state.cascade_count,
        "previousProgress": previous_progress, "newProgress": state.progression,
        "previousLevel": level - 1, "newLevel": level,
        "thresholdReached": rules.thresholds[level - 2],
        "stage": rules.stages[level - 1],
        "isFinalLevel": level == len(rules.stages),
    })
