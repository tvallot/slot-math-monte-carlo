"""Kitchen vs Fly: math engine of a cluster-pays game with in-round progression.

Importing this package makes the Stake Engine math SDK (git submodule in
vendor/math-sdk) importable, and redirects every SDK output (books, lookup
tables, configs) to <repo>/output/games instead of the SDK's own games/ folder.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SDK_ROOT = REPO_ROOT / "vendor" / "math-sdk"
OUTPUT_ROOT = REPO_ROOT / "output"

if not (SDK_ROOT / "src").is_dir():
    raise ImportError(
        "Stake Engine math SDK not found. Run: git submodule update --init"
    )
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

# The SDK binds PATH_TO_GAMES by value at import time (src/config/config.py and
# src/config/output_filenames.py), so it must be patched before they are imported.
import src.config.paths as _sdk_paths  # noqa: E402

_sdk_paths.PATH_TO_GAMES = str(OUTPUT_ROOT / "games")
