from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from example.gnn_kg.train import main as train_main


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--allow-cpu", action="store_true")
    args, rest = parser.parse_known_args()
    sys.argv = [sys.argv[0], "--smoke-test", *rest]
    if args.allow_cpu:
        sys.argv.append("--allow-cpu")
    train_main()
