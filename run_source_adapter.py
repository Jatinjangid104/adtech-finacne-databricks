#!/usr/bin/env python3
"""Turn notebook-03 dumps into vendor-shaped source landings. Does not change 03."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "generator_src"))

from source_adapter import republish, topic_catalog  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--from",
        dest="src",
        default=str(ROOT / "data" / "landing"),
        help="Generator output (notebook 03 / run_local.py)",
    )
    parser.add_argument(
        "--to",
        dest="dst",
        default=str(ROOT / "data" / "sources"),
        help="Vendor-shaped landings",
    )
    parser.add_argument("--catalog", action="store_true", help="Print topic map and exit")
    args = parser.parse_args()

    if args.catalog:
        print(json.dumps(topic_catalog(), indent=2))
        return

    counts = republish(args.src, args.dst)
    print(json.dumps({"generator_root": args.src, "sources_root": args.dst, "counts": counts}, indent=2))
    if not counts:
        print("No generator files found. Run notebook 03 or: python3 run_local.py --iterations 1")


if __name__ == "__main__":
    main()
