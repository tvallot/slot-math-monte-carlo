"""Simulate a library of rounds, write the SDK output files and validate them.

    python -m kitchen_vs_fly.run --sims 10000

Books, lookup tables and configs are written to output/games/kitchen_vs_fly/library.
"""

import argparse
import json
import os
from pathlib import Path
import shutil

from kitchen_vs_fly import OUTPUT_ROOT
from kitchen_vs_fly.game_config import production_config
from kitchen_vs_fly.gamestate import GameState
from kitchen_vs_fly.validation import validate_library
from src.state.run_sims import create_books
from src.write_data.write_configs import generate_configs
from utils.rgs_verification import execute_all_tests


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sims", type=int, default=1000, help="number of simulated rounds (1 to 100,000)")
    args = parser.parse_args()
    if not 1 <= args.sims <= 100_000:
        parser.error("--sims must be between 1 and 100,000")

    config = production_config()  # 7x7 production configuration
    # The SDK writes stats_summary.json to a path relative to the working directory.
    OUTPUT_ROOT.mkdir(exist_ok=True)
    os.chdir(OUTPUT_ROOT)

    state = GameState(config)
    # A single worker avoids silent child-process failures.
    create_books(state, config, {"base": args.sims}, args.sims, 1, True, False)
    # Unit-weight lookup table: every simulated round has the same weight.
    paths = state.output_files.lookups["base"]["paths"]
    shutil.copyfile(paths["base_lookup"], paths["optimized_lookup"])
    generate_configs(state)
    execute_all_tests(config)
    report = validate_library(config, args.sims)
    Path(config.library_path, "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
