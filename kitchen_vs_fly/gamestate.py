"""One base round, using the SDK's cluster evaluation, gravity and win manager."""

import random

from kitchen_vs_fly.game_events import (
    cascade_event, cluster_win_event, progress_update_event, level_up_event,
    wild_added_event, grid_transform_event, multiplier_zone_created_event, multiplier_upgraded_event,
    boss_triggered_event, boss_shrunk_event, boss_defeated_event, golden_fly_started_event,
    fly_minibonus_triggered_event,
)
from src.calculations.cluster import Cluster
from src.events.events import reveal_event, update_tumble_win_event
from src.executables.executables import Executables


class GameState(Executables):
    def assign_special_sym_function(self):
        self.special_symbol_functions = {}

    def reset_fs_spin(self):
        # The SDK constructor invokes this hook even for games with no free spins.
        pass

    def run_freespin(self):
        raise RuntimeError("Free spins are not part of this prototype")

    def reset_book(self):
        super().reset_book()
        self.cascade_count = 0
        self.progression = 0
        self.level = 1
        self.fly_minibonus_activated = False
        self.n2_wilds_activated = False
        self.n3_activated = False
        self.n4_activated = False
        self.n4_multipliers = {}  # {(reel, row): value}; position-owned, survives symbol removal.
        # N5 boss state machine: 3x3 -> 2x2 -> 1x1 -> defeated (see
        # activate_n5_boss below). n5_boss_awaiting_evaluation and
        # n5_boss_advance_pending are internal flags, not gameplay state exposed
        # to events; they exist only to guarantee exactly one dedicated boss
        # evaluation per size, after which the boss ALWAYS advances (V0.3.21:
        # progression never depends on a win overlapping the footprint).
        self.n5_boss_active = False
        self.n5_boss_defeated = False
        self.n5_boss_size = 0
        self.n5_boss_anchor = None
        self.n5_boss_positions = set()  # current footprint; also protected from explode/gravity.
        self.n5_boss_awaiting_evaluation = False
        self.n5_boss_advance_pending = False
        # Golden Mega Fly (V0.3.21): decided once, at the boss trigger.
        self.golden_active = False
        self.golden_multiplier = 1
        self.sticky_positions = set()
        # Positions that held a WFLY Wild at the moment of the most recent
        # evaluate_board() win, snapshotted there (see evaluate_board) since
        # WFLY, unlike sticky_positions/n5_boss_positions, is actually
        # removed by its own win and would otherwise be gone by the time
        # advance_progression runs.
        self.fly_wild_win_positions = set()
        self.reel_positions = []
        self.reelstrip = []
        self.new_symbols_from_tumble = [[] for _ in range(self.config.num_reels)]
        self.board_before_tumble = []
        self.refresh_special_syms()

    def draw_board(self):
        self.create_board_reelstrips()
        reveal_event(self)

    def evaluate_board(self):
        # Re-stamp position-owned N4 multiplier values onto whatever symbol
        # currently occupies each position: gravity/refill create fresh
        # Symbol objects with no multiplier attribute, so the value would
        # otherwise be lost even though the position must keep it. The SDK's
        # own cluster evaluation already sums this attribute across a
        # cluster's positions (additive stacking, floored at 1), which is
        # exactly the N4 V1 payout rule (see GAME_DESIGN.md).
        self.apply_n4_multipliers()
        # Golden Mega Fly: its multiplier applies only to the one dedicated
        # evaluation of each boss size (3x3, 2x2, 1x1), as the SDK's own
        # global_multiplier, so winInfo/payout stay a direct event product.
        boss_evaluation = self.n5_boss_active and self.n5_boss_awaiting_evaluation
        global_multiplier = self.golden_multiplier if (boss_evaluation and self.golden_active) else 1
        self.win_data = Cluster.get_cluster_data(self.config, self.board, global_multiplier=global_multiplier)
        self.complete_n5_boss_evaluation()
        # Both N2 sticky Wilds and the N5 Mega Wild footprint must survive a
        # winning evaluation intact: neither is a payable symbol removed by
        # its own win (see GAME_DESIGN.md, "Mega Wild positions are not
        # independently payable symbols").
        for c, r in self.sticky_positions | self.n5_boss_positions:
            self.board[c][r].explode = False
        Cluster.record_cluster_wins(self)
        # Snapshot which of this evaluation's winning positions are WFLY
        # Wilds while the board still reflects pre-tumble identities (WFLY
        # is an ordinary, non-sticky symbol, so ordinary gravity may remove
        # or relocate it before advance_progression runs for this win).
        self.fly_wild_win_positions = {
            (p["reel"], p["row"])
            for win in self.win_data["wins"]
            for p in win["positions"]
            if self.board[p["reel"]][p["row"]].name == "WFLY"
        }
        self.win_manager.update_spinwin(self.win_data["totalWin"])
        self.win_manager.tumble_win = self.win_data["totalWin"]
        if self.win_data["totalWin"] > 0:
            cluster_win_event(self, global_multiplier)
            self.upgrade_n4_multipliers()
            update_tumble_win_event(self)
            self.evaluate_wincap()

    def run_spin(self, sim, simulation_seed=None):
        self.reset_seed(sim, seed_override=simulation_seed)
        self.repeat = True
        while self.repeat:
            self.reset_book()
            self.play_round(sim)
            self.check_repeat()
        self.imprint_wins()

    def play_round(self, sim):
        self.draw_board()
        self.activate_fly_minibonus()
        progress_update_event(self, 0, 1, "roundStart")
        self.evaluate_board()
        # An active boss always has a next step (shrink/defeat) even when its
        # dedicated evaluation paid nothing, so the loop must not stop on a
        # zero-win evaluation while N5 is unfinished (V0.3.21).
        while (self.win_data["totalWin"] > 0 or self.n5_boss_advance_pending) and not self.wincap_triggered:
            if self.cascade_count >= self.config.max_cascades:
                raise RuntimeError(f"Cascade guard exceeded for simulation {sim}")
            if self.win_data["totalWin"] > 0:
                self.tumble_board()
                self.cascade_count += 1
                cascade_event(self)
                self.advance_progression(self.win_data["wins"])
                self.activate_n2_wilds()
                self.activate_n3_conversion()
                self.activate_n4()
            self.activate_n5_boss()
            self.evaluate_board()
        self.set_end_tumble_event()
        self.win_manager.update_gametype_wins(self.gametype)
        self.evaluate_finalwin()

    def check_repeat(self):
        """SDK criteria acceptance (win_criteria) plus this game's own
        force_golden condition: a forced-Golden distribution only accepts a
        round in which the Golden Mega Fly actually started. Without a
        configured betmode/criteria (direct unit-test use) a round is always
        accepted, exactly as before V0.3.21."""
        self.repeat = False
        if not self.criteria:
            return
        distribution = self.get_current_betmode_distributions()
        win_criteria = distribution.get_win_criteria()
        if win_criteria is not None and self.final_win != win_criteria:
            self.repeat = True
        if distribution._conditions.get("force_golden") and not self.golden_active:
            self.repeat = True
        # Bonus Buy (V0.3.22): a purchased round is a complete natural round
        # that reached N5 without Golden. Only a criteria carrying `force_n5`
        # is affected; base-mode criteria never set it.
        if distribution._conditions.get("force_n5") and not (
                (self.n5_boss_active or self.n5_boss_defeated) and not self.golden_active):
            self.repeat = True
        if self.repeat:
            self.repeat_count += 1
            self.check_current_repeat_count()

    def advance_progression(self, wins):
        """Called once after removal/refill, before evaluating the next grid."""
        # Neither an N2 sticky Wild nor a current N5 Mega Wild cell is ever
        # actually removed by the win that touched it, so neither counts as a
        # "distinct winning position destroyed" for progression purposes.
        # A WFLY Wild IS removed like any ordinary symbol when it wins, but
        # its progression credit is withheld by design (V0.3.17): placing
        # 3-5 extra Wilds on the still-blank initial board otherwise lets a
        # single first-round cascade jump through several progression
        # thresholds at once, far beyond the mini-bonus's intended immediate
        # payout value on the current spin only (see
        # docs/milestones/V0.3.17_kitchen_7x7_fly_minibonus.md).
        excluded = self.sticky_positions | self.n5_boss_positions | self.fly_wild_win_positions
        removed_wins = [
            {**win, "positions": [p for p in win["positions"]
                                   if (p["reel"], p["row"]) not in excluded]}
            for win in wins
        ]
        gain = self.config.progression_rules.gain(removed_wins)
        if type(gain) is not int or gain < 0:
            raise ValueError("Progression gain must be a non-negative integer")
        previous_progress, previous_level = self.progression, self.level
        self.progression += gain
        self.level = self.config.progression_rules.level_for(self.progression)
        progress_update_event(self, previous_progress, previous_level, "symbolsRemoved")
        for level in range(previous_level + 1, self.level + 1):
            level_up_event(self, previous_progress, level)

    def activate_fly_minibonus(self):
        """Rolled at most once per round, right after draw_board() and
        before the round's first evaluate_board() (see run_spin) — not
        level-gated, since by design this fires before N1 progression could
        possibly have unlocked anything (see game_config.py,
        docs/milestones/V0.3.8_symbol_frequencies_fly_minibonus_20_40_60_80.md).
        Always emits exactly one flyMiniBonusTriggered event (triggered or
        not) so replay can tell the two cases apart unambiguously. On
        trigger, config.fly_wild_counts (3, 4 or 5, equal probability by
        default) distinct cells of the still-pristine initial board become
        temporary "WFLY" Wilds; no placement-exclusion set is needed the way
        N2/N4/N5 need one, because nothing else has touched the board yet at
        this point in the round. A WFLY Wild needs no dedicated consumption
        bookkeeping for explode/gravity either: it is an ordinary, non-sticky
        board symbol, so the engine's normal explode/gravity path removes it
        automatically the instant it takes part in a paid win, and simply
        leaves it in place — available to a later cascade — otherwise,
        exactly like N4's own "WN4" Wild minus the persistent multiplier
        state N4 also has to track. It DOES need dedicated bookkeeping for
        progression, though (see evaluate_board's fly_wild_win_positions
        snapshot and advance_progression, V0.3.17): its win positions are
        deliberately excluded from progression credit even though the
        symbol itself is genuinely removed."""
        if not self.config.fly_minibonus_enabled or self.fly_minibonus_activated:
            return
        self.fly_minibonus_activated = True
        triggered = random.random() < self.config.fly_trigger_probability
        wild_count, changes = 0, []
        if triggered:
            wild_count = random.choice(self.config.fly_wild_counts)
            candidates = sorted((c, r) for c, column in enumerate(self.board) for r in range(len(column)))
            selected = sorted(random.sample(candidates, min(wild_count, len(candidates))))
            changes = [{"reel": c, "row": r, "fromSymbol": self.board[c][r].name, "toSymbol": "WFLY"}
                       for c, r in selected]
            for c, r in selected:
                self.board[c][r] = self.create_symbol("WFLY")
            self.get_special_symbols_on_board()
        fly_minibonus_triggered_event(self, triggered, wild_count, changes)

    def activate_n2_wilds(self):
        """Once per round, when N2 (roast chicken protection) is reached: a
        configured number of persistent Wilds appear and stay through the
        remaining cascades of the round. Generalizes the old Armor
        sticky-Wild mechanism (removed in V0.3.2, see docs/milestones/
        V0.3.2_kitchen_n2_persistent_wilds.md) at N2's own level (2).

        Optionally (`config.n2_temp_wild_count`, default 0), also places a
        second, temporary Wild type ("WN2T") alongside the sticky ones: an
        ordinary, non-sticky board symbol exactly like N4's "WN4" or the Fly
        mini-bonus's "WFLY" — removed by the engine's normal explode/gravity
        the instant it takes part in a paid win, persisting otherwise. No
        dedicated N5-avoidance is needed for it (unlike the sticky "W" set),
        matching WN4/WFLY's own lack of special protection."""
        if not self.config.n2_wild_enabled or self.n2_wilds_activated or self.level < 2:
            return
        count = self.config.n2_wild_count
        if type(count) is not int or count < 0:
            raise ValueError("N2 Wild count must be a non-negative integer")
        temp_count = self.config.n2_temp_wild_count
        if type(temp_count) is not int or temp_count < 0:
            raise ValueError("N2 temporary Wild count must be a non-negative integer")
        candidates = [(c, r) for c, col in enumerate(self.board) for r, symbol in enumerate(col)
                      if symbol.name in self.config.symbols and (c, r) not in self.sticky_positions]
        selected = sorted(random.sample(candidates, min(count, len(candidates))))
        transformed = [{"reel": c, "row": r, "fromSymbol": self.board[c][r].name, "toSymbol": "W"}
                       for c, r in selected]
        temp_selected = []
        if temp_count:
            # No random.sample call at all when temp_count == 0 (the
            # default), so RNG consumption stays byte-identical to every
            # pre-existing caller of this method.
            temp_candidates = [pos for pos in candidates if pos not in set(selected)]
            temp_selected = sorted(random.sample(temp_candidates, min(temp_count, len(temp_candidates))))
        temp_transformed = [{"reel": c, "row": r, "fromSymbol": self.board[c][r].name, "toSymbol": "WN2T"}
                             for c, r in temp_selected]
        self.n2_wilds_activated = True
        for c, r in selected:
            self.board[c][r] = self.create_symbol("W")
        for c, r in temp_selected:
            self.board[c][r] = self.create_symbol("WN2T")
        self.sticky_positions.update(selected)
        self.get_special_symbols_on_board()
        wild_added_event(self, transformed, temp_transformed)

    def activate_n3_conversion(self):
        """Once per round, when N3 (active fly hunting) is reached: inspect
        the premium types (H1-H4) currently present on the board, pick one of
        them as the target, and convert every other premium type into it in
        place. Low symbols and N2 persistent Wilds are never touched, since
        neither has a name in config.premium_symbols. N3 removes nothing and
        creates no empty cell, so there is no gravity/refill step here: the
        transformed board is simply left for run_spin's trailing
        evaluate_board() to reevaluate immediately (updated design, see
        GAME_DESIGN.md — this replaces the earlier remove/refill mechanic)."""
        if not self.config.n3_conversion_enabled or self.n3_activated or self.level < 3:
            return
        present = sorted({symbol.name for column in self.board for symbol in column
                           if symbol.name in self.config.premium_symbols})
        target = random.choice(present) if present else None
        transformed = sorted(
            (c, r) for c, column in enumerate(self.board) for r, symbol in enumerate(column)
            if symbol.name in self.config.premium_symbols and symbol.name != target
        )
        positions = [{"reel": c, "row": r, "fromSymbol": self.board[c][r].name, "toSymbol": target}
                     for c, r in transformed]
        self.n3_activated = True
        for c, r in transformed:
            self.board[c][r] = self.create_symbol(target)
        grid_transform_event(self, present, target, positions)

    def select_n4_zone_top_left(self):
        """Isolated placement rule (DEV/PROVISIONAL, see GAME_DESIGN.md):
        uniform draw among every valid contiguous top-left corner for the
        configured zone size, using the round's deterministic RNG. Kept as
        its own method so an alternative rule (weighted, forced-corner, ...)
        can be swapped in later without touching the rest of activate_n4()."""
        size = self.config.n4_zone_size
        max_reel = self.config.num_reels - size
        max_row = min(self.config.num_rows) - size
        placements = sorted((c, r) for c in range(max_reel + 1) for r in range(max_row + 1))
        return random.choice(placements)

    def apply_n4_multipliers(self):
        """Stamp the position-owned N4 multiplier values onto the symbols
        currently occupying those positions. Must run before every cluster
        evaluation for the rest of the round (see evaluate_board).

        Every other symbol's "multiplier" attribute is cleared first. This
        game's gravity (tumble_board, above) reuses surviving Symbol objects
        by reference as they shift within a column instead of recreating
        them; without clearing, a symbol that sat in the zone, did not win,
        and was later shifted out of the zone by an unrelated removal
        elsewhere in its column would keep carrying its old multiplier value
        forever, silently inflating unrelated future wins. The multiplier
        belongs to the position, never to whatever symbol currently sits on it."""
        for column in self.board:
            for symbol in column:
                if symbol.multiplier is not None:
                    symbol.assign_attribute({"multiplier": None})
        for (c, r), value in self.n4_multipliers.items():
            self.board[c][r].assign_attribute({"multiplier": value})

    def activate_n4(self):
        """Once per round, when N4 (determination) is reached: create a
        contiguous 3x3 multiplier zone (nine positions immediately at x2,
        independent of whatever symbol sits there), replace three distinct
        zone positions with temporary N4 Wilds ("WN4", never sticky), then
        let run_spin's trailing evaluate_board() reevaluate immediately —
        same no-removal/no-gravity-here pattern as N3. N2 persistent Wilds
        inside the zone are never overwritten: Wild positions are chosen
        only among the zone's non-sticky positions (see GAME_DESIGN.md,
        design/FEATURE_IDEAS.md)."""
        if not self.config.n4_multiplier_enabled or self.n4_activated or self.level < 4:
            return
        size = self.config.n4_zone_size
        top_c, top_r = self.select_n4_zone_top_left()
        zone = sorted((c, r) for c in range(top_c, top_c + size) for r in range(top_r, top_r + size))
        initial = self.config.n4_multiplier_initial
        for pos in zone:
            self.n4_multipliers[pos] = initial
        candidates = sorted(pos for pos in zone if pos not in self.sticky_positions)
        wild_positions = sorted(random.sample(candidates, min(self.config.n4_wild_count, len(candidates))))
        wild_changes = [{"reel": c, "row": r, "fromSymbol": self.board[c][r].name} for c, r in wild_positions]
        self.n4_activated = True
        for c, r in wild_positions:
            self.board[c][r] = self.create_symbol("WN4")
        self.apply_n4_multipliers()
        self.get_special_symbols_on_board()
        multiplier_zone_created_event(self, top_c, top_r, zone, initial, wild_changes)

    def upgrade_n4_multipliers(self):
        """After a paid evaluation, every N4 multiplier position that
        actually took part in a paid winning cluster steps up by one
        (capped). This runs after get_cluster_data(): the payout just
        computed already used the pre-upgrade values, so only cascades after
        this one see the new value (see GAME_DESIGN.md). A position can only
        belong to one paid cluster per evaluation — the SDK's flood fill
        never assigns a cell to two different clusters within one
        get_clusters() call — so each participating position upgrades
        exactly once here regardless of how many wins touched it."""
        if not self.n4_multipliers:
            return
        cap = self.config.n4_multiplier_cap
        participating = {(p["reel"], p["row"]) for win in self.win_data["wins"] for p in win["positions"]}
        changes = []
        for pos in sorted(participating & self.n4_multipliers.keys()):
            previous = self.n4_multipliers[pos]
            new = min(previous + 1, cap)
            if new != previous:
                self.n4_multipliers[pos] = new
                changes.append({"reel": pos[0], "row": pos[1], "previousValue": previous, "newValue": new})
        if changes:
            multiplier_upgraded_event(self, changes)

    def n5_boss_candidate_placements(self, size):
        """All valid top-left corners for a `size`x`size` footprint on the
        grid, excluding any that would overlap a current N2 persistent Wild
        position. Applied to every boss size (not just the initial 3x3):
        the Mega Wild must never silently overwrite N2's persistent state
        (see GAME_DESIGN.md), mirroring N4's own exclusion of sticky
        positions from its temporary-Wild placement."""
        max_reel = self.config.num_reels - size
        max_row = min(self.config.num_rows) - size
        placements = []
        for c in range(max_reel + 1):
            for r in range(max_row + 1):
                footprint = {(x, y) for x in range(c, c + size) for y in range(r, r + size)}
                if footprint.isdisjoint(self.sticky_positions):
                    placements.append((c, r))
        return sorted(placements)

    def select_n5_initial_top_left(self):
        """Initial 3x3 placement (DEV/PROVISIONAL, see GAME_DESIGN.md):
        uniform draw among the N2-safe candidates that do not overlap the N4
        zone, when at least one such candidate exists.

        On this 6x6 grid, every one of the 16 possible 3x3 top-left corners
        overlaps N4's zone whenever N4 itself sits at one of the four fully
        interior corners: (1,1), (1,2), (2,1) or (2,2) (proven exhaustively
        in test_all_n4_placements_permit_a_valid_or_minimal_overlap_boss_placement).
        Rather than silently allowing the full 9-cell zone to overlap in that
        case, this falls back to the candidates with the fewest overlapping
        cells — provably always exactly 1 cell when the zero-overlap set is
        empty — uniform among ties. When N4 is disabled/not yet placed
        (self.n4_multipliers empty), any N2-safe candidate is used."""
        size = self.config.n5_boss_size
        candidates = self.n5_boss_candidate_placements(size)
        if not candidates:
            raise RuntimeError("No valid initial 3x3 boss placement avoids every N2 Wild")
        if not self.n4_multipliers:
            return random.choice(candidates)
        zone = set(self.n4_multipliers.keys())

        def overlap(top_left):
            c, r = top_left
            footprint = {(x, y) for x in range(c, c + size) for y in range(r, r + size)}
            return len(footprint & zone)

        overlaps = {p: overlap(p) for p in candidates}
        best = min(overlaps.values())
        return random.choice(sorted(p for p, v in overlaps.items() if v == best))

    def select_n5_relocation_top_left(self, size, previous_anchor):
        """2x2/1x1 relocation (DEV/PROVISIONAL): uniform draw among
        placements that (a) never overlap an N2 persistent Wild and (b) move
        the anchor by at least one reel or row from the previous size's
        anchor (mandatory movement rule, see GAME_DESIGN.md). N4 overlap is
        explicitly allowed at these sizes, unlike the initial 3x3."""
        candidates = [p for p in self.n5_boss_candidate_placements(size) if p != previous_anchor]
        if not candidates:
            raise RuntimeError(f"No valid {size}x{size} boss relocation avoids every N2 Wild while moving")
        return random.choice(candidates)

    def complete_n5_boss_evaluation(self):
        """Called from evaluate_board() on EVERY evaluation, win or not. The
        evaluation dedicated to the boss's current size is consumed here
        unconditionally and the boss is then due to advance (shrink, or be
        defeated after the 1x1 evaluation) on the next step, whether or not
        any winning position touched its footprint (V0.3.21). A size is
        therefore evaluated exactly once and never freezes."""
        if not self.n5_boss_active or not self.n5_boss_awaiting_evaluation:
            return
        self.n5_boss_awaiting_evaluation = False
        self.n5_boss_advance_pending = True

    def activate_n5_boss(self):
        """Once per round, when N5 (boss) is reached: create the initial 3x3
        Mega Wild footprint. On a later step, once the size's dedicated
        evaluation has happened (complete_n5_boss_evaluation, called from
        evaluate_board), shrink and relocate the footprint here
        (3x3 -> 2x2 -> 1x1 -> defeated), using an
        explode-then-tumble-then-event pattern, immediately before the
        trailing evaluate_board() reevaluates at the
        new size. See GAME_DESIGN.md and
        docs/milestones/V0.3.5_kitchen_n5_mega_wild_fly.md."""
        if not self.config.n5_boss_enabled or self.n5_boss_defeated or self.level < 5:
            return
        if not self.n5_boss_active:
            self._trigger_n5_boss()
            return
        if not self.n5_boss_advance_pending:
            return
        self.n5_boss_advance_pending = False
        new_size = self.n5_boss_size - 1
        if new_size == 0:
            self._defeat_n5_boss()
        else:
            self._shrink_and_relocate_n5_boss(new_size)

    def _trigger_n5_boss(self):
        size = self.config.n5_boss_size
        top_c, top_r = self.select_n5_initial_top_left()
        footprint = {(c, r) for c in range(top_c, top_c + size) for r in range(top_r, top_r + size)}
        self.n5_boss_active = True
        self.n5_boss_size = size
        self.n5_boss_anchor = (top_c, top_r)
        self.n5_boss_positions = footprint
        self.n5_boss_awaiting_evaluation = True
        for c, r in footprint:
            self.board[c][r] = self.create_symbol("WN5")
        self.get_special_symbols_on_board()
        boss_triggered_event(self, top_c, top_r, size, sorted(footprint))
        self.start_golden_fly()

    def forced_golden(self):
        if not self.criteria:
            return False
        conditions = self.get_current_distribution_conditions()
        return isinstance(conditions, dict) and bool(conditions.get("force_golden"))

    def start_golden_fly(self):
        """Golden Mega Fly (V0.3.21), decided once at the boss trigger by the
        math simulation (seeded, serialized as goldenFlyStarted): either forced
        by the criteria's force_golden condition (the special GOLDEN/WINCAP
        distributions) or, when config.golden_enabled, by a configured
        probability roll. It only attaches a multiplier to the three dedicated
        boss evaluations; it never changes N5's trigger or shape."""
        config = self.config
        if self.forced_golden():
            pass
        elif not (config.golden_enabled and random.random() < config.golden_trigger_probability):
            return
        assert config.golden_ladder, "Golden Mega Fly needs a configured multiplier ladder"
        multipliers, weights = zip(*config.golden_ladder)
        self.golden_active = True
        self.golden_multiplier = random.choices(multipliers, weights)[0]
        golden_fly_started_event(self, self.golden_multiplier)

    def _shrink_and_relocate_n5_boss(self, new_size):
        """Only the vacated cells (old minus new footprint) explode/refill
        through the normal gravity path; entered cells (new minus old) get a
        fresh WN5 stamped in place after that tumble, exactly like N4's own
        in-place Wild placement. Retained cells (old and new) are simply left
        alone. self.n5_boss_positions is updated to the new footprint BEFORE
        tumble_board() runs so vacated cells are treated as ordinary mobile
        cells by it (see tumble_board below) while entered/retained cells
        stay protected as boss cells throughout (see GAME_DESIGN.md,
        "gravity must not independently scatter individual cells")."""
        previous_size, previous_anchor = self.n5_boss_size, self.n5_boss_anchor
        previous_footprint = self.n5_boss_positions
        top_c, top_r = self.select_n5_relocation_top_left(new_size, previous_anchor)
        new_footprint = {(c, r) for c in range(top_c, top_c + new_size) for r in range(top_r, top_r + new_size)}
        vacated = previous_footprint - new_footprint
        entered = new_footprint - previous_footprint
        self.n5_boss_positions = new_footprint
        for c, r in vacated:
            self.board[c][r].explode = True
        if self.cascade_count >= self.config.max_cascades:
            raise RuntimeError(f"Cascade guard exceeded for simulation {self.sim}")
        self.tumble_board()
        for c, r in entered:
            self.board[c][r] = self.create_symbol("WN5")
        self.get_special_symbols_on_board()
        self.n5_boss_size, self.n5_boss_anchor = new_size, (top_c, top_r)
        self.n5_boss_awaiting_evaluation = True
        # Informational event first, tumbleBoard second, both already
        # reflecting the final post-move board: same ordering convention
        # used for this feature's own dedicated tumble above.
        boss_shrunk_event(self, previous_size, new_size, previous_anchor, (top_c, top_r),
                           sorted(previous_footprint), sorted(new_footprint), sorted(vacated), sorted(entered))
        self.cascade_count += 1
        cascade_event(self, n5_positions=[{"reel": c, "row": r} for c, r in sorted(vacated)])

    def _defeat_n5_boss(self):
        previous_size, previous_anchor = self.n5_boss_size, self.n5_boss_anchor
        previous_footprint = self.n5_boss_positions
        self.n5_boss_positions = set()
        for c, r in previous_footprint:
            self.board[c][r].explode = True
        if self.cascade_count >= self.config.max_cascades:
            raise RuntimeError(f"Cascade guard exceeded for simulation {self.sim}")
        self.tumble_board()
        self.n5_boss_active = False
        self.n5_boss_defeated = True
        self.n5_boss_size, self.n5_boss_anchor = 0, None
        self.get_special_symbols_on_board()
        boss_defeated_event(self, previous_size, previous_anchor, sorted(previous_footprint))
        self.cascade_count += 1
        cascade_event(self, n5_positions=[{"reel": c, "row": r} for c, r in sorted(previous_footprint)])

    def tumble_board(self):
        fixed = self.sticky_positions | self.n5_boss_positions
        if not fixed:
            return super().tumble_board()
        # Compact only mobile slots. A fixed Wild/boss cell is skipped, not a column barrier.
        self.board_before_tumble = [col[:] for col in self.board]
        self.new_symbols_from_tumble = [[] for _ in self.board]
        for c, column in enumerate(self.board):
            mobile_rows = [r for r in range(len(column)) if (c, r) not in fixed]
            survivors = [column[r] for r in mobile_rows if not column[r].explode]
            missing = len(mobile_rows) - len(survivors)
            for _ in range(missing):
                self.reel_positions[c] = (self.reel_positions[c] - 1) % len(self.reelstrip[c])
                symbol = self.create_symbol(self.reelstrip[c][self.reel_positions[c]])
                self.new_symbols_from_tumble[c].insert(0, symbol)
            mobile_symbols = self.new_symbols_from_tumble[c] + survivors
            new_column = column[:]
            for r, symbol in zip(mobile_rows, mobile_symbols):
                new_column[r] = symbol
            for r in range(len(column)):
                if (c, r) in fixed:
                    new_column[r].explode = False
            self.board[c] = new_column
        self.get_special_symbols_on_board()
