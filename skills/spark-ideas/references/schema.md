# Snapshot contract (version 1)

This is a projection of current committed Spark text, not the App database. A source is a random installation UUID, not a hardware or account identifier.

Exact snapshot fields:

| Field | Meaning |
| --- | --- |
| `schemaVersion` | Integer `1` (not a boolean). |
| `sourceInstallationID` | Hyphenated UUID for the exporting installation. |
| `generatedAt` | ISO 8601 timestamp with timezone, years 0001–9999. |
| `isEnabled` | Boolean; `false` requires an empty `ideas` array. |
| `scope` | `favorites` or `all`. |
| `ideas` | Array of record objects, maximum 20,000. |

Exact record fields are `id` (unique UUID), `createdAt` and `updatedAt` (timestamps), `kind` (`originalVoice` or `aiSummary`), `isFavorite` (boolean), and `body` (nonblank Markdown string). A `favorites` snapshot may only contain favorite records. Clocks can move: no relative ordering of timestamps is imposed.

The maximum serialized file is 16 MiB. Each body is limited to 256 KiB in UTF-8, and total body bytes cannot exceed 16 MiB. Unknown keys, duplicate JSON keys, invalid types/UUIDs/dates, malformed encoding, repeated IDs, and inconsistent revocation/range data are rejected. A newer schema needs a reviewed reader update, not a permissive fallback. Source/original history, deleted records, audio, recovery metadata, credentials, and models do not belong in this contract. Sensitive text manually included in a selected body is still exported; this is not a secret scrubber.

## Reader output

All successful responses include `snapshot` metadata (version, source UUID, timestamp, scope, enabled state, SHA-256 `fingerprint` of the exact validated bytes) and `currentContentUntrusted: true`.

- `status`: `status`, `available`, `noteCount`; no body or excerpt. Disabled export is a successful status result with `available: false` and zero records.
- `list`/`search`: `total`, `offset`, `nextOffset`, and `ideas` with metadata and excerpts up to 240 Unicode code points. Defaults to 20 records, at most 100 per page. Results sort by creation date descending, then UUID ascending. `--offset` advances a page.
- `search --query`: a literal, case-insensitive match in Markdown bodies; not regex, semantic search, or a network request. Query length is 1–1,024 characters and at most 4,096 UTF-8 bytes.
- `get --id`: `idea` metadata, `body`, `bodyOffset`, `bodyCharacters`, and `nextBodyOffset`. Default chunk is 4,000, maximum 16,000 Unicode code points. Use `--body-offset` for the next chunk; offsets refer to code points, not bytes or rendered Markdown elements.

Every invocation rereads one current local file. With no saved configuration or explicit path, discovery checks only direct UUID folders under `~/Library/Mobile Documents/iCloud~com~jinhan~Spark/Documents/Agents` for `ideas.json`. Discovery uses file metadata, not note bodies; it is bounded to 64 source folders and 256 directory entries. One source is selected automatically. Multiple sources return `source_selection_required` with `sources` containing only `sourceID` and file `modifiedAt`; the user must choose, never implicitly select the newest or merge sources. Other iCloud folders are not searched.

The reader uses bounded regular-file access, rejects a leaf symlink, and detects file changes during the read. Automatically found sources also reject symlinked directory paths and require the snapshot source UUID to match its folder. On macOS it asks Foundation whether the selected file is ubiquitous; an iCloud item must have `current` download status before and after reading. Metadata-check failure is an error. It never proactively downloads data. These checks cannot prove that a future iPhone export has already reached the Mac.

Errors are JSON `{ "error": { "code": "...", "message": "..." } }` with exit code 1, without paths or underlying file details. Discovery can additionally return the `sources` array above. Codes include `invalid_arguments`, `snapshot_unavailable`, `snapshot_syncing`, `snapshot_changed`, `snapshot_invalid`, `unsupported_schema`, `export_disabled`, `idea_not_found`, `output_too_large`, `configuration_unavailable`, `no_snapshot`, `source_not_found`, `source_selection_required`, `too_many_sources`, and `discovery_unavailable`.

## Local configuration

After a connection request, `configure_snapshot.py --auto` remembers the single discovered source in `~/.config/spark-ideas/config.json` with private file permissions. For multiple sources, use `--auto --source <UUID>` after the user chooses. Advanced explicit paths remain supported with `--snapshot <absolute path>`. Only the path is saved; configuration does not open the snapshot. Existing settings require explicit `--replace`.

`read_snapshot.py` uses an explicit `--snapshot` first, otherwise the saved configuration, and only discovers when the configuration file is absent. A broken configuration or missing pinned file is an error, never permission to choose another source. `--source <UUID>` can select an initially discovered source but cannot override an existing pin. Both helpers accept `--config <absolute path>` for isolated tests or an explicitly chosen alternative. Nothing is written into the snapshot or skill folder. Changing a saved source requires the user's confirmation; do not reuse another user's example path.

The supported user workflow is macOS with personal iCloud Drive. Non-macOS test runs can exercise synthetic ordinary files; they do not establish Windows iCloud support.
