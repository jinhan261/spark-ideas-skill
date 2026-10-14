#!/usr/bin/env python3
"""Remember one explicitly selected path; never open or alter the snapshot."""

import json
import os
from pathlib import Path
import sys
import tempfile

from read_snapshot import SafeParser, SnapshotError, default_config, selected_path


def main(argv=None):
    temporary = None
    try:
        parser = SafeParser(description="Explicitly save one selected Spark file path, without reading its contents.")
        parser.add_argument("--snapshot", required=True)
        parser.add_argument("--config", default=default_config())
        parser.add_argument("--replace", action="store_true", help="Explicitly replace an existing local path setting")
        args = parser.parse_args(argv)
        snapshot = selected_path(args.snapshot)
        target = Path(selected_path(args.config))
        if target.is_symlink():
            raise SnapshotError("configuration_unavailable", "The local configuration is not a regular setting file.")
        if target.exists() and not args.replace:
            raise SnapshotError("configuration_unavailable", "A path setting already exists. Confirm the new source and use --replace to change it.")
        if target.exists() and not target.is_file():
            raise SnapshotError("configuration_unavailable", "The local configuration is not a regular setting file.")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".spark-config-", dir=target.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"snapshot": snapshot}, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        if args.replace:
            os.replace(temporary, target)
        else:
            # Link creation is atomic and refuses even a concurrently created target.
            os.link(temporary, target)
            os.unlink(temporary)
        temporary = None
        print(json.dumps({"configured": True, "noteContentRead": False}))
        return 0
    except SnapshotError as error:
        print(json.dumps({"error": {"code": error.code, "message": error.message}}, sort_keys=True))
        return 1
    except OSError:
        print(json.dumps({"error": {"code": "configuration_unavailable", "message": "The local path setting could not be saved."}}, sort_keys=True))
        return 1
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
