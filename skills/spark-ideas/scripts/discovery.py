"""Discover only Spark's fixed iCloud Agents directory, without reading contents."""

import datetime as dt
import os
from pathlib import Path
import stat
import uuid

MAX_SOURCES = 64
MAX_DIRECTORY_ENTRIES = 256


class DiscoveryError(Exception):
    def __init__(self, code, message, sources=None):
        self.code = code
        self.message = message
        self.sources = sources
        super().__init__(message)


def default_root():
    return Path.home() / "Library" / "Mobile Documents" / "iCloud~com~jinhan~Spark" / "Documents" / "Agents"


def source_id(value):
    try:
        identifier = uuid.UUID(value)
        if str(identifier) != value.lower():
            raise ValueError()
        return str(identifier).upper()
    except (ValueError, TypeError, AttributeError):
        raise DiscoveryError("invalid_arguments", "Choose a source UUID listed by Spark discovery.") from None


def directory_identity(info):
    return info.st_dev, info.st_ino, info.st_mode


def root_chain_guard(root):
    """Check the fixed chain from the user's home, not unrelated iCloud folders."""
    root = Path(root)
    chain = [root]
    if root.parts[-5:] == ("Library", "Mobile Documents", "iCloud~com~jinhan~Spark", "Documents", "Agents"):
        chain += list(root.parents[:5])
    identity = []
    for directory in chain:
        info = os.lstat(directory)
        if not stat.S_ISDIR(info.st_mode):
            raise DiscoveryError("discovery_unavailable", "Spark's snapshot location contains an unsafe directory. No files are read.")
        identity.append(directory_identity(info))
    return tuple(identity)


def no_snapshot():
    raise DiscoveryError("no_snapshot", "No Spark snapshot has arrived. Enable Agent support in Spark, use the same Apple account with iCloud Drive on this Mac, then retry after synchronization.")


def discover_snapshot(root=None, source=None):
    """Return one exact file, or metadata-only source choices; never choose newest."""
    root = Path(root) if root is not None else default_root()
    selected = source_id(source) if source is not None else None
    root_fd = None
    try:
        try:
            root_before = os.lstat(root)
            chain_before = root_chain_guard(root)
        except FileNotFoundError:
            no_snapshot()
        if not stat.S_ISDIR(root_before.st_mode):
            raise DiscoveryError("discovery_unavailable", "Spark's snapshot directory is not a regular directory. No files are read.")
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        if directory_identity(os.fstat(root_fd)) != directory_identity(root_before):
            raise DiscoveryError("snapshot_changed", "Spark's snapshot directory changed. Retry discovery.")
        root_listing_time = root_before.st_mtime_ns, root_before.st_ctime_ns
        candidates = []
        source_count = 0
        with os.scandir(root_fd) as entries:
            for entry_count, entry in enumerate(entries, 1):
                if entry_count > MAX_DIRECTORY_ENTRIES:
                    raise DiscoveryError("too_many_sources", "Spark has too many source entries. Choose an explicit file instead of automatic discovery.")
                try:
                    identifier = source_id(entry.name)
                except DiscoveryError:
                    continue
                source_count += 1
                if source_count > MAX_SOURCES:
                    raise DiscoveryError("too_many_sources", "Spark has too many device sources. Choose an explicit file instead of automatic discovery.")
                if selected is not None and identifier != selected:
                    continue
                source_info = os.stat(entry.name, dir_fd=root_fd, follow_symlinks=False)
                if not stat.S_ISDIR(source_info.st_mode):
                    raise DiscoveryError("discovery_unavailable", "A Spark source is not a regular directory. No files are read.")
                source_fd = None
                try:
                    source_fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
                    if directory_identity(os.fstat(source_fd)) != directory_identity(source_info):
                        raise DiscoveryError("snapshot_changed", "A Spark source changed during discovery. Retry.")
                    try:
                        file_info = os.stat("ideas.json", dir_fd=source_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if not stat.S_ISREG(file_info.st_mode):
                        raise DiscoveryError("discovery_unavailable", "A Spark snapshot is not a regular file. No files are read.")
                    if directory_identity(os.stat(entry.name, dir_fd=root_fd, follow_symlinks=False)) != directory_identity(source_info):
                        raise DiscoveryError("snapshot_changed", "A Spark source changed during discovery. Retry.")
                    candidates.append({
                        "path": str(root / entry.name / "ideas.json"),
                        "sourceID": identifier,
                        "modifiedAt": dt.datetime.fromtimestamp(file_info.st_mtime, dt.timezone.utc).isoformat(),
                    })
                finally:
                    if source_fd is not None:
                        os.close(source_fd)
        root_after = os.lstat(root)
        if (root_chain_guard(root) != chain_before
                or directory_identity(root_after) != directory_identity(root_before)
                or (root_after.st_mtime_ns, root_after.st_ctime_ns) != root_listing_time):
            raise DiscoveryError("snapshot_changed", "Spark's snapshot directory changed. Retry discovery.")
        if not candidates:
            if selected is not None:
                raise DiscoveryError("source_not_found", "The chosen Spark source has no available snapshot. Wait for iCloud Drive and retry.")
            no_snapshot()
        if len(candidates) > 1:
            choices = [{"sourceID": item["sourceID"], "modifiedAt": item["modifiedAt"]}
                       for item in sorted(candidates, key=lambda item: item["sourceID"])]
            raise DiscoveryError("source_selection_required", "More than one Spark source is available. Ask the user which device source to use; do not choose by date.", choices)
        return candidates[0]
    except DiscoveryError:
        raise
    except (OSError, ValueError, OverflowError):
        raise DiscoveryError("discovery_unavailable", "Spark's snapshot directory could not be checked. No files are read.") from None
    finally:
        if root_fd is not None:
            os.close(root_fd)
