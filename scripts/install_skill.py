#!/usr/bin/env python3
"""Copy this self-contained skill into one explicitly selected client directory."""

import argparse
import json
from pathlib import Path
import shutil
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install Spark ideas skill only; no MCP registration or note reads.")
    parser.add_argument("--client", required=True, choices=("codex", "claude"))
    parser.add_argument("--destination", help="Absolute skill destination directory, for explicit customization or tests")
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parent.parent / "skills" / "spark-ideas"
    destination = Path(args.destination) if args.destination else Path.home() / (".agents" if args.client == "codex" else ".claude") / "skills" / "spark-ideas"
    try:
        if not destination.is_absolute() or destination.exists() or destination.is_symlink():
            raise ValueError()
        if not (source / "SKILL.md").is_file():
            raise ValueError()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(json.dumps({"installed": True, "client": args.client, "destination": str(destination), "noteContentRead": False, "mcpConfigurationChanged": False}))
        return 0
    except (OSError, ValueError):
        print(json.dumps({"error": {"code": "installation_unavailable", "message": "Installation requires a new absolute destination and a complete skill source. Existing skills are not overwritten."}}, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
