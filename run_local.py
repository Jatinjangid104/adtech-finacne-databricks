#!/usr/bin/env python3
"""Run the AdTech generator on this machine. Writes JSON under data/landing/."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "generator_src"
os.chdir(ROOT)
sys.path.insert(0, str(SRC))

from core.config_loader import ConfigLoader  # noqa: E402
from orchestrator import GeneratorOrchestrator  # noqa: E402
from validation.schema_validator import SchemaValidator  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Local AdTech event generator")
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Batches to emit. Use 0 for continuous (Ctrl+C to stop).",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "data" / "landing"),
        help="Output folder (default: data/landing)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    config_path = SRC / "config" / "generator_config.yaml"
    overrides = {"platform": {"output_base_path": str(out)}}
    config = ConfigLoader(str(config_path), overrides=overrides).load().config
    validator = SchemaValidator(config, max_record_age_hours=72)

    print(f"repo          : {ROOT}")
    print(f"output        : {out}")
    print(f"seed          : {config.platform.seed}")
    print(f"user pool     : {config.users.total_pool_size}")
    print(f"campaigns     : {config.campaigns.count}")

    orchestrator = GeneratorOrchestrator(config, validator)
    max_iterations = None if args.iterations == 0 else args.iterations
    orchestrator.run(max_iterations=max_iterations)


if __name__ == "__main__":
    main()
