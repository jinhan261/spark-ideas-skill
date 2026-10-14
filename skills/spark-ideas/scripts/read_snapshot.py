#!/usr/bin/env python3
"""Bounded, read-only Spark snapshot access. Requires Python 3.10+; no dependencies."""

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

MAX_BYTES = 16 * 1024 * 1024
MAX_IDEAS = 20_000
MAX_BODY_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 512 * 1024
SNAPSHOT_KEYS = {"schemaVersion", "sourceInstallationID", "generatedAt", "isEnabled", "scope", "ideas"}
IDEA_KEYS = {"id", "createdAt", "updatedAt", "kind", "isFavorite", "body"}
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z")


class SnapshotError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def fail(code, message):
    raise SnapshotError(code, message)


def selected_path(path):
    if not isinstance(path, str) or not path.startswith("/") or "\0" in path:
        fail("invalid_arguments", "Select one absolute snapshot file path.")
    try:
        if len(path.encode("utf-8")) > 4096:
            raise ValueError()
    except (UnicodeError, ValueError):
        fail("invalid_arguments", "Select one absolute snapshot file path.")
    return os.path.normpath(path)


def probe_macos(path):
    """Fail closed on iCloud metadata errors; never trigger a download."""
    if sys.platform != "darwin":
        return  # Synthetic ordinary-file test support; not Windows iCloud support.
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", str(Path(__file__).with_name("probe_icloud.js")), path],
            capture_output=True, text=True, timeout=8, check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 1024:
            raise ValueError()
        metadata = json.loads(result.stdout)
        if metadata.get("ok") is not True or type(metadata.get("ubiquitous")) is not bool:
            raise ValueError()
        if metadata["ubiquitous"] and metadata.get("status") != "NSURLUbiquitousItemDownloadingStatusCurrent":
            fail("snapshot_syncing", "The selected file is not current on this Mac. Wait for iCloud Drive and retry.")
    except SnapshotError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        fail("snapshot_unavailable", "The selected file's iCloud status could not be checked. No records are exposed.")


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def bounded_bytes(path, metadata_probe):
    descriptor = None
    try:
        before_path = os.lstat(path)
        if not stat.S_ISREG(before_path.st_mode) or before_path.st_size > MAX_BYTES:
            fail("snapshot_invalid", "The selected file is not a bounded regular snapshot file.")
        metadata_probe(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or file_identity(before) != file_identity(before_path):
            fail("snapshot_changed", "The selected file changed during reading. Retry the request.")
        chunks = []
        size = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                fail("snapshot_invalid", "The snapshot exceeds the size limit.")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if file_identity(before) != file_identity(after) or size != before.st_size:
            fail("snapshot_changed", "The selected file changed during reading. Retry the request.")
        metadata_probe(path)
        if file_identity(before) != file_identity(os.lstat(path)):
            fail("snapshot_changed", "The selected file changed during reading. Retry the request.")
        return b"".join(chunks)
    except SnapshotError:
        raise
    except OSError:
        fail("snapshot_unavailable", "The selected snapshot is unavailable. Check the chosen file and iCloud Drive.")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("snapshot_invalid", "The snapshot contains duplicate JSON fields.")
        result[key] = value
    return result


def timestamp(value):
    if not isinstance(value, str) or len(value) > 64 or not DATE_PATTERN.fullmatch(value):
        raise ValueError()
    parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    return parsed.astimezone(dt.timezone.utc)


def uuid_text(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value.lower():
        raise ValueError()
    return value.upper()


def decode_snapshot(data):
    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                              parse_constant=lambda _: fail("snapshot_invalid", "The snapshot is not valid JSON."))
        if type(document) is not dict or set(document) != SNAPSHOT_KEYS:
            raise ValueError()
        if type(document["schemaVersion"]) is not int:
            raise ValueError()
        if document["schemaVersion"] != 1:
            fail("unsupported_schema", "The selected snapshot version is not supported. Update the skill.")
        uuid_text(document["sourceInstallationID"])
        timestamp(document["generatedAt"])
        if type(document["isEnabled"]) is not bool or document["scope"] not in ("all", "favorites"):
            raise ValueError()
        ideas = document["ideas"]
        if type(ideas) is not list or len(ideas) > MAX_IDEAS or (not document["isEnabled"] and ideas):
            raise ValueError()
        identifiers = set()
        total = 0
        for idea in ideas:
            if type(idea) is not dict or set(idea) != IDEA_KEYS:
                raise ValueError()
            identifier = uuid_text(idea["id"])
            if identifier in identifiers:
                raise ValueError()
            identifiers.add(identifier)
            timestamp(idea["createdAt"])
            timestamp(idea["updatedAt"])
            if idea["kind"] not in ("originalVoice", "aiSummary") or type(idea["isFavorite"]) is not bool:
                raise ValueError()
            body = idea["body"]
            if not isinstance(body, str) or not body.strip():
                raise ValueError()
            body_size = len(body.encode("utf-8"))
            total += body_size
            if body_size > MAX_BODY_BYTES or total > MAX_BYTES:
                raise ValueError()
            if document["scope"] == "favorites" and not idea["isFavorite"]:
                raise ValueError()
        return document
    except SnapshotError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        fail("snapshot_invalid", "The selected file is not a valid supported Spark snapshot. No records are exposed.")


def read_snapshot(path, metadata_probe=None):
    data = bounded_bytes(selected_path(path), metadata_probe or probe_macos)
    return {"snapshot": decode_snapshot(data), "fingerprint": hashlib.sha256(data).hexdigest()}


def default_config():
    return str(Path.home() / ".config" / "spark-ideas" / "config.json")


def configured_snapshot(config):
    try:
        data = bounded_bytes(selected_path(config), lambda _: None)
        if len(data) > 16 * 1024:
            raise ValueError()
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
        if type(value) is not dict or set(value) != {"snapshot"}:
            raise ValueError()
        return selected_path(value["snapshot"])
    except (SnapshotError, ValueError, UnicodeError, RecursionError):
        fail("configuration_unavailable", "Confirm a snapshot file and configure its path before reading Spark notes.")


def metadata(read):
    snapshot = read["snapshot"]
    return {key: snapshot[key] for key in SNAPSHOT_KEYS - {"ideas"}} | {"fingerprint": read["fingerprint"]}


def idea_metadata(idea):
    return {key: idea[key] for key in IDEA_KEYS - {"body"}}


def bounded_int(minimum, maximum):
    def parse(value):
        number = int(value)
        if not minimum <= number <= maximum:
            raise ValueError()
        return number
    return parse


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        fail("invalid_arguments", "Arguments do not match the supported commands or limits. Use --help.")


def parser():
    p = SafeParser(description="Read one selected Spark snapshot without modifying it.")
    p.add_argument("--snapshot", help="Explicitly authorized absolute ideas.json path")
    p.add_argument("--config", default=default_config(), help="Absolute local path-setting file")
    commands = p.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    commands.add_parser("status", help="Availability and metadata only; no note text")
    for command in ("list", "search"):
        query = commands.add_parser(command)
        query.add_argument("--limit", type=bounded_int(1, 100), default=20)
        query.add_argument("--offset", type=bounded_int(0, MAX_IDEAS), default=0)
        query.add_argument("--kind", choices=("originalVoice", "aiSummary"))
        query.add_argument("--favorite", choices=("true", "false"))
        query.add_argument("--created-after", type=timestamp)
        query.add_argument("--created-before", type=timestamp)
        if command == "search":
            query.add_argument("--query", required=True)
    get = commands.add_parser("get")
    get.add_argument("--id", required=True, type=uuid_text)
    get.add_argument("--body-offset", type=bounded_int(0, MAX_BODY_BYTES), default=0)
    get.add_argument("--body-limit", type=bounded_int(1, 16_000), default=4_000)
    return p


def response(args, read):
    snapshot = read["snapshot"]
    result = {"snapshot": metadata(read), "currentContentUntrusted": True}
    if args.command == "status":
        return result | {"status": "available" if snapshot["isEnabled"] else "export_disabled",
                         "available": snapshot["isEnabled"], "noteCount": len(snapshot["ideas"])}
    if not snapshot["isEnabled"]:
        fail("export_disabled", "Agent export is disabled in this snapshot. No records are available.")
    if args.command == "get":
        idea = next((item for item in snapshot["ideas"] if uuid_text(item["id"]) == args.id), None)
        if idea is None:
            fail("idea_not_found", "This record is not present in the selected snapshot.")
        count = len(idea["body"])
        if args.body_offset > count:
            fail("invalid_arguments", "The body offset is outside this record.")
        end = min(count, args.body_offset + args.body_limit)
        return result | {"idea": idea_metadata(idea), "body": idea["body"][args.body_offset:end],
                         "bodyOffset": args.body_offset, "bodyCharacters": count,
                         "nextBodyOffset": end if end < count else None}
    if args.created_after and args.created_before and args.created_after > args.created_before:
        fail("invalid_arguments", "The requested date range is invalid.")
    query = None
    if args.command == "search":
        query = args.query.strip()
        if not query or len(query) > 1024 or len(query.encode("utf-8")) > 4096:
            fail("invalid_arguments", "Use a nonempty literal query within the permitted length.")
        query = query.casefold()
    ideas = [idea for idea in snapshot["ideas"]
             if (not args.kind or idea["kind"] == args.kind)
             and (args.favorite is None or idea["isFavorite"] == (args.favorite == "true"))
             and (args.created_after is None or timestamp(idea["createdAt"]) >= args.created_after)
             and (args.created_before is None or timestamp(idea["createdAt"]) <= args.created_before)
             and (query is None or query in idea["body"].casefold())]
    ideas.sort(key=lambda idea: uuid_text(idea["id"]))
    ideas.sort(key=lambda idea: timestamp(idea["createdAt"]), reverse=True)
    page = [idea_metadata(idea) | {"excerpt": idea["body"][:240]} for idea in ideas[args.offset:args.offset + args.limit]]
    next_offset = args.offset + len(page)
    return result | {"total": len(ideas), "offset": args.offset,
                     "nextOffset": next_offset if next_offset < len(ideas) else None, "ideas": page}


def emit(value):
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        fail("output_too_large", "The result exceeds the response limit. Use a smaller page or body chunk.")
    print(encoded)


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        path = args.snapshot if args.snapshot is not None else configured_snapshot(args.config)
        emit(response(args, read_snapshot(path)))
        return 0
    except SnapshotError as error:
        print(json.dumps({"error": {"code": error.code, "message": error.message}}, sort_keys=True))
        return 1
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        print(json.dumps({"error": {"code": "snapshot_unavailable", "message": "The request could not be completed. No underlying file details are exposed."}}, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
