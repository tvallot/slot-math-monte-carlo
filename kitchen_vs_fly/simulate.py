"""Monte Carlo simulation: play N rounds and save one row per round for analysis.

    python -m kitchen_vs_fly.simulate --sims 1000000                     # 7x7 production
    python -m kitchen_vs_fly.simulate --sims 200000 --config 6x6-candidate-a

Round i is played with random.seed(i + 1) by the SDK, so every run is reproducible.
The output (data/rounds_<config>.csv.gz) has one row per round:

    round_id     index of the simulated round
    payout       total win, as a multiple of the bet
    level        highest progression level reached in the round (1 to 5)
    cascades     number of cascades (tumbles) in the round
    fly          1 if the Fly mini-bonus triggered
    boss_defeated 1 if the N5 boss was defeated
"""

import argparse
import csv
import gzip
import io
import json
import os
from pathlib import Path
import time

import zstandard

from kitchen_vs_fly import OUTPUT_ROOT, REPO_ROOT
from kitchen_vs_fly.game_config import GameConfig, production_config
from kitchen_vs_fly.gamestate import GameState
from src.state.run_sims import create_books

CONFIGS = {
    "7x7-production": production_config,
    "6x6-candidate-a": lambda: GameConfig(production=True),
}
BATCH_SIZE = 50_000
COLUMNS = ["round_id", "payout", "level", "cascades", "fly", "boss_defeated"]


def summarize(book):
    """Reduce one simulated round (an SDK 'book') to a flat row."""
    counts = {}
    fly = 0
    for event in book["events"]:
        counts[event["type"]] = counts.get(event["type"], 0) + 1
        # The Fly event is emitted on every round; `triggered` says whether it fired.
        if event["type"] == "flyMiniBonusTriggered" and event.get("triggered"):
            fly = 1
    return [
        book["id"],
        book["payoutMultiplier"] / 100,
        1 + counts.get("levelUp", 0),
        counts.get("tumbleBoard", 0),
        fly,
        int(counts.get("bossDefeated", 0) > 0),
    ]


def iter_books(path):
    with open(path, "rb") as raw:
        reader = zstandard.ZstdDecompressor().stream_reader(raw)
        for line in io.TextIOWrapper(reader, encoding="utf-8"):
            if line.strip():
                yield json.loads(line)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sims", type=int, default=100_000)
    parser.add_argument("--config", choices=sorted(CONFIGS), default="7x7-production")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    # The SDK keeps a whole batch of rounds in memory before writing it.
    batch = min(args.sims, BATCH_SIZE)
    if args.sims % batch:
        parser.error(f"--sims must be a multiple of {BATCH_SIZE:,} above that size")
    out = (args.out or REPO_ROOT / "data" / f"rounds_{args.config}.csv.gz").resolve()

    config = CONFIGS[args.config]()
    OUTPUT_ROOT.mkdir(exist_ok=True)
    os.chdir(OUTPUT_ROOT)  # the SDK writes some files relative to the working directory

    start = time.time()
    state = GameState(config)
    create_books(state, config, {"base": args.sims}, batch, 1, True, False)
    books = Path(config.publish_path) / "books_base.jsonl.zst"
    print(f"Simulated {args.sims:,} rounds in {time.time() - start:.0f} s")

    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for book in iter_books(books):
            writer.writerow(summarize(book))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
