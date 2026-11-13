"""Configurable round-only progression; no equipment powers or random draws."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressionRules:
    # Development-scale thresholds, kept from V0.3 to preserve validated engine
    # behaviour; NOT the conceptual Kitchen vs Fly 25/50/75/100 (or 20/40/60/80)
    # thresholds, which remain provisional and unsimulated (see GAME_DESIGN.md).
    thresholds: tuple[int, ...] = (5, 10, 15, 20)
    # Neutral Kitchen vs Fly level labels. N5 is the final level: no "ultimate"
    # or further stage exists beyond it (no N6 by design).
    stages: tuple[str, ...] = ("n1_kitchen", "n2_protection", "n3_hunt", "n4_determination", "n5_boss")
    points_per_symbol: int = 1

    def __post_init__(self):
        if len(self.stages) != len(self.thresholds) + 1 or not self.stages:
            raise ValueError("Each level requires a stage; level 1 has no threshold")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("Stage identifiers must be unique")
        if any(type(t) is not int or t <= 0 for t in self.thresholds):
            raise ValueError("Thresholds must be positive integers")
        if any(a >= b for a, b in zip(self.thresholds, self.thresholds[1:])):
            raise ValueError("Thresholds must be strictly increasing")
        if type(self.points_per_symbol) is not int or self.points_per_symbol <= 0:
            raise ValueError("Points per symbol must be a positive integer")

    def gain(self, wins: list[dict]) -> int:
        """One credit per distinct winning position, even if clusters overlap later.

        Override this method in a rules subclass to experiment with another
        accumulation method without changing the round loop.
        """
        positions = {(p["reel"], p["row"]) for win in wins for p in win["positions"]}
        return len(positions) * self.points_per_symbol

    def level_for(self, progress: int) -> int:
        return 1 + sum(progress >= threshold for threshold in self.thresholds)

    def levels(self) -> list[dict]:
        return [{"level": i + 1, "stage": stage, "threshold": threshold}
                for i, (stage, threshold) in enumerate(zip(self.stages, (0,) + self.thresholds))]
