"""Read-only reader tests. Every record and path is synthetic and temporary."""
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]
READER_PATH = ROOT / "skills/spark-ideas/scripts/read_snapshot.py"
SOURCE_ID = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
FIRST_ID = "10000000-0000-4000-8000-000000000001"
SECOND_ID = "10000000-0000-4000-8000-000000000002"
PRIVATE_MARKER = "SYNTHETIC_PRIVATE_MARKER_DO_NOT_LEAK"


def record(identifier=FIRST_ID, **updates):
    result = {"id": identifier, "createdAt": "2026-10-14T10:00:00Z",
              "updatedAt": "2026-10-14T11:00:00Z", "kind": "originalVoice",
              "isFavorite": False, "body": "# 中文想法\n\n- **PCB** 设计\n- MCP 连接\n"}
    result.update(updates)
    return result


def snapshot(ideas=None, **updates):
    result = {"schemaVersion": 1, "sourceInstallationID": SOURCE_ID,
              "generatedAt": "2026-10-14T12:00:00Z", "isEnabled": True,
              "scope": "all", "ideas": [record()] if ideas is None else ideas}
    result.update(updates)
    return result


def load_module():
    spec = importlib.util.spec_from_file_location("spark_snapshot_reader", READER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = load_module()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="spark-skill-test-")
        self.directory = Path(self.temp.name)
        self.path = self.directory / "synthetic-ideas.json"
        self.write(snapshot())

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        self.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def read(self):
        return self.reader.read_snapshot(str(self.path), metadata_probe=lambda _: None)

    def cli(self, *arguments, expected=0):
        result = subprocess.run([sys.executable, str(READER_PATH), "--snapshot",
                                 str(self.path), *arguments], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def assert_rejected(self, value):
        self.write(value)
        with self.assertRaises(self.reader.SnapshotError):
            self.read()

    def test_valid_utf8_markdown_and_fingerprint(self):
        result = self.read()
        self.assertEqual(result["snapshot"]["ideas"][0]["body"], record()["body"])
        self.assertRegex(result["fingerprint"], r"^[a-f0-9]{64}$")

    def test_status_never_contains_record_body(self):
        self.write(snapshot([record(body=PRIVATE_MARKER)]))
        result = self.cli("status")
        self.assertTrue(result["available"])
        self.assertEqual(result["noteCount"], 1)
        self.assertNotIn(PRIVATE_MARKER, json.dumps(result))
        self.assertNotIn("ideas", result)
        self.assertNotIn("body", result)
        self.assertTrue(result["currentContentUntrusted"])

    def test_disabled_status_and_read_fail_closed(self):
        self.write(snapshot([], isEnabled=False))
        self.assertFalse(self.cli("status")["available"])
        for command in (("list",), ("search", "--query", "PCB"),
                        ("get", "--id", FIRST_ID)):
            with self.subTest(command=command):
                self.assertEqual(self.cli(*command, expected=1)["error"]["code"],
                                 "export_disabled")

    def test_empty_enabled_snapshot(self):
        self.write(snapshot([]))
        result = self.cli("list")
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["ideas"], [])

    def test_list_newest_first_pagination_and_excerpt(self):
        self.write(snapshot([record(), record(SECOND_ID, createdAt="2026-10-14T11:30:00Z",
                                             body="晚一点的记录" * 100)]))
        result = self.cli("list", "--limit", "1")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["ideas"][0]["id"].upper(), SECOND_ID)
        self.assertLessEqual(len(result["ideas"][0]["excerpt"]), 241)
        self.assertEqual(result["nextOffset"], 1)
        next_page = self.cli("list", "--offset", "1", "--limit", "1")
        self.assertEqual(next_page["ideas"][0]["id"].upper(), FIRST_ID)
        self.assertIsNone(next_page["nextOffset"])

    def test_filters_kind_favorite_and_dates(self):
        self.write(snapshot([record(), record(SECOND_ID, kind="aiSummary", isFavorite=True,
                                             createdAt="2026-10-14T11:30:00Z")]))
        result = self.cli("list", "--kind", "aiSummary", "--favorite", "true",
                          "--created-after", "2026-10-14T11:00:00Z",
                          "--created-before", "2026-10-14T12:00:00Z")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["ideas"][0]["id"].upper(), SECOND_ID)

    def test_search_is_literal_and_unicode(self):
        self.write(snapshot([record(body="PCB *literal* 中文主题"),
                             record(SECOND_ID, body="No matching data")]))
        for query in ("中文", "*literal*", "pcb"):
            with self.subTest(query=query):
                result = self.cli("search", "--query", query)
                self.assertEqual(result["total"], 1)
        self.assertEqual(self.cli("search", "--query", ".*")["total"], 0)

    def test_get_unicode_characters_and_chunk_continuation(self):
        body = "标题：PCB 🧠\n- **重点**\n" * 2000
        self.write(snapshot([record(body=body)]))
        result = self.cli("get", "--id", FIRST_ID.lower(), "--body-limit", "10")
        self.assertEqual(result["body"], body[:10])
        self.assertEqual(result["bodyCharacters"], len(body))
        self.assertEqual(result["nextBodyOffset"], 10)
        continuation = self.cli("get", "--id", FIRST_ID, "--body-offset", "10",
                                "--body-limit", "10")
        self.assertEqual(continuation["body"], body[10:20])

    def test_invalid_cli_ranges_fail_without_traceback(self):
        for arguments in (("list", "--limit", "101"), ("list", "--offset", "-1"),
                          ("get", "--id", FIRST_ID, "--body-limit", "16001"),
                          ("get", "--id", FIRST_ID, "--body-offset", "-1")):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, str(READER_PATH), "--snapshot",
                                         str(self.path), *arguments], capture_output=True,
                                        text=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_schema_bool_integer_type_confusion(self):
        for key, value in (("schemaVersion", True), ("schemaVersion", 1.0),
                           ("isEnabled", 1), ("isEnabled", "true")):
            with self.subTest(key=key, value=value):
                self.assert_rejected(snapshot(**{key: value}))
        for value in (0, 1, "false", None):
            with self.subTest(favorite=value):
                self.assert_rejected(snapshot([record(isFavorite=value)]))

    def test_unsupported_missing_and_unknown_fields(self):
        self.assert_rejected(snapshot(schemaVersion=2))
        for key in snapshot():
            value = snapshot()
            del value[key]
            with self.subTest(missing=key):
                self.assert_rejected(value)
        for key in record():
            value = snapshot()
            del value["ideas"][0][key]
            with self.subTest(missing_idea=key):
                self.assert_rejected(value)
        for target in ("snapshot", "idea"):
            value = snapshot()
            destination = value if target == "snapshot" else value["ideas"][0]
            destination["apiKey"] = PRIVATE_MARKER
            with self.subTest(unknown=target):
                self.assert_rejected(value)
        for kind in ("trash", "summary", 3):
            self.assert_rejected(snapshot([record(kind=kind)]))
        self.assert_rejected(snapshot(scope="deleted"))

    def test_UUID_validation_and_duplicate_case_insensitive(self):
        for bad in ("not-a-uuid", "", 123, None):
            with self.subTest(uuid=bad):
                self.assert_rejected(snapshot(sourceInstallationID=bad))
                self.assert_rejected(snapshot([record(bad)]))
        self.assert_rejected(snapshot([record(), record(FIRST_ID.lower())]))

    def test_invalid_and_portable_dates(self):
        for bad in ("2026-02-30T12:00:00Z", "2026-01-01", "2026-10-14T25:00:00Z",
                    "2026-10-14T12:00:00", "0000-01-01T00:00:00Z", "invalid", 1, None):
            with self.subTest(date=bad):
                self.assert_rejected(snapshot(generatedAt=bad))
                self.assert_rejected(snapshot([record(updatedAt=bad)]))
        self.write(snapshot([record(createdAt="9999-12-31T23:59:59Z",
                                    updatedAt="0001-01-01T00:00:00Z")],
                            generatedAt="0001-01-01T00:00:00Z"))
        self.read()  # Clock changes must not impose chronological ordering.

    def test_empty_nonstring_and_unpaired_surrogate_bodies(self):
        for body in ("", " \t\n", [], None, 12, "\ud800"):
            with self.subTest(body=repr(body)):
                value = snapshot([record(body=body)])
                self.path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(self.reader.SnapshotError):
                    self.read()

    def test_body_byte_limit_including_multibyte(self):
        self.write(snapshot([record(body="a" * (256 * 1024))]))
        self.read()
        self.assert_rejected(snapshot([record(body="a" * (256 * 1024 + 1))]))
        self.assert_rejected(snapshot([record(body="中" * (256 * 1024 // 3 + 1))]))

    def test_record_count_boundary(self):
        records = [record(str(uuid.UUID(int=index + 1)), body="x") for index in range(20000)]
        self.write(snapshot(records))
        self.read()
        records.append(record(str(uuid.UUID(int=20001)), body="x"))
        self.assert_rejected(snapshot(records))

    def test_favorites_and_revoked_constraints(self):
        self.assert_rejected(snapshot(scope="favorites"))
        self.assert_rejected(snapshot(isEnabled=False))
        self.write(snapshot([record(isFavorite=True)], scope="favorites"))
        self.read()

    def test_invalid_utf8_json_duplicate_keys_and_top_level(self):
        for raw in (b"\xff", b"{", b"[]", b"null", b'{"schemaVersion":1,"schemaVersion":1}'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(self.reader.SnapshotError):
                    self.read()

    def test_file_size_limit_rejects_before_reading_large_file(self):
        with self.path.open("wb") as file:
            file.truncate(16 * 1024 * 1024 + 1)
        with self.assertRaises(self.reader.SnapshotError):
            self.read()

    def test_symlink_directory_and_FIFO_rejected_without_blocking(self):
        alias = self.directory / "alias.json"
        alias.symlink_to(self.path)
        fifo = self.directory / "pipe.json"
        os.mkfifo(fifo)
        for path in (alias, self.directory, fifo):
            with self.subTest(path=path.name):
                with self.assertRaises(self.reader.SnapshotError):
                    self.reader.read_snapshot(str(path), metadata_probe=lambda _: None)

    def test_errors_do_not_output_sensitive_body_or_absolute_path(self):
        self.write(snapshot([record(body=PRIVATE_MARKER)], apiKey=PRIVATE_MARKER))
        result = self.cli("status", expected=1)
        output = json.dumps(result)
        self.assertNotIn(PRIVATE_MARKER, output)
        self.assertNotIn(str(self.directory), output)
        self.assertNotIn("Traceback", output)

    def test_rereads_current_file_without_old_cache(self):
        first = self.read()
        replacement = self.directory / "replacement.json"
        replacement.write_text(json.dumps(snapshot([record(body="new content")]),
                                          ensure_ascii=False), encoding="utf-8")
        os.replace(replacement, self.path)
        second = self.read()
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(second["snapshot"]["ideas"][0]["body"], "new content")
        self.path.unlink()
        with self.assertRaises(self.reader.SnapshotError):
            self.read()
        self.path.write_text("corrupt", encoding="utf-8")
        with self.assertRaises(self.reader.SnapshotError):
            self.read()

    def test_metadata_failure_is_fail_closed_without_body(self):
        def unavailable(_):
            raise self.reader.SnapshotError("syncing", "Synthetic availability check failed.")
        with self.assertRaises(self.reader.SnapshotError):
            self.reader.read_snapshot(str(self.path), metadata_probe=unavailable)

    def test_snapshot_change_during_final_probe_is_rejected(self):
        calls = 0
        def replace_during_probe(_):
            nonlocal calls
            calls += 1
            if calls == 2:
                replacement = self.directory / "race.json"
                replacement.write_text(json.dumps(snapshot([record(body="race replacement")])),
                                       encoding="utf-8")
                os.replace(replacement, self.path)
        with self.assertRaises(self.reader.SnapshotError):
            self.reader.read_snapshot(str(self.path), metadata_probe=replace_during_probe)

    def test_in_place_write_during_read_is_rejected(self):
        self.write(snapshot([record(body="AAAAAAAAAAA")]))
        original_read = self.reader.os.read
        changed = False
        def mutate_after_read(descriptor, amount):
            nonlocal changed
            data = original_read(descriptor, amount)
            if data and not changed:
                changed = True
                self.write(snapshot([record(body="BBBBBBBBBBB")]))
            return data
        with mock.patch.object(self.reader.os, "read", side_effect=mutate_after_read):
            with self.assertRaises(self.reader.SnapshotError):
                self.read()


class ConfigurationAndInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="spark-skill-setup-test-")
        self.directory = Path(self.temp.name)
        self.snapshot = self.directory / "synthetic-ideas.json"
        self.snapshot.write_text(json.dumps(snapshot([record(body=PRIVATE_MARKER)])),
                                 encoding="utf-8")
        self.config = self.directory / "settings" / "config.json"

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, path, *arguments, expected=0):
        result = subprocess.run([sys.executable, str(path), *arguments],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def configure(self, *extra, expected=0):
        return self.run_script(ROOT / "skills/spark-ideas/scripts/configure_snapshot.py",
                               "--snapshot", str(self.snapshot), "--config", str(self.config),
                               *extra, expected=expected)

    def test_configure_remembers_one_path_without_reading_or_modifying_note(self):
        before = self.snapshot.read_bytes()
        result = self.configure()
        self.assertTrue(result["configured"])
        self.assertFalse(result["noteContentRead"])
        self.assertNotIn(PRIVATE_MARKER, json.dumps(result))
        self.assertEqual(self.snapshot.read_bytes(), before)
        self.assertEqual(json.loads(self.config.read_text()), {"snapshot": str(self.snapshot)})
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        response = self.run_script(READER_PATH, "--config", str(self.config), "status")
        self.assertTrue(response["available"])
        self.assertNotIn(PRIVATE_MARKER, json.dumps(response))

    def test_configuration_accepts_unavailable_path_but_reader_fails_closed(self):
        self.snapshot.unlink()
        self.configure()
        result = self.run_script(READER_PATH, "--config", str(self.config), "status", expected=1)
        self.assertIn("error", result)
        self.assertNotIn(str(self.directory), json.dumps(result))

    def test_configuration_does_not_overwrite_without_explicit_replace(self):
        self.config.parent.mkdir()
        self.config.write_text('{"snapshot":"/synthetic/old/ideas.json"}', encoding="utf-8")
        before = self.config.read_bytes()
        self.configure(expected=1)
        self.assertEqual(self.config.read_bytes(), before)
        self.configure("--replace")
        self.assertEqual(json.loads(self.config.read_text()), {"snapshot": str(self.snapshot)})

    def test_symlink_config_is_never_overwritten(self):
        target = self.directory / "other-config.json"
        target.write_text("keep me", encoding="utf-8")
        self.config.parent.mkdir()
        self.config.symlink_to(target)
        self.configure("--replace", expected=1)
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(target.read_text(), "keep me")

    def test_concurrent_config_creation_is_not_overwritten_without_replace(self):
        reader = load_module()
        path = ROOT / "skills/spark-ideas/scripts/configure_snapshot.py"
        spec = importlib.util.spec_from_file_location("spark_configure_for_test", path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"read_snapshot": reader}):
            spec.loader.exec_module(module)
        original_mkstemp = module.tempfile.mkstemp
        existing = '{"snapshot":"/synthetic/concurrent/ideas.json"}'
        def create_concurrent_config(*arguments, **keywords):
            result = original_mkstemp(*arguments, **keywords)
            self.config.write_text(existing, encoding="utf-8")
            return result
        output = io.StringIO()
        with mock.patch.object(module.tempfile, "mkstemp", side_effect=create_concurrent_config), \
             contextlib.redirect_stdout(output):
            code = module.main(["--snapshot", str(self.snapshot), "--config", str(self.config)])
        self.assertEqual(code, 1)
        self.assertEqual(self.config.read_text(), existing)
        self.assertNotIn(str(self.directory), output.getvalue())

    def test_invalid_config_never_falls_back_to_any_other_file(self):
        self.config.parent.mkdir()
        for value in ([], {"snapshot": str(self.snapshot), "unexpected": PRIVATE_MARKER},
                      {"snapshot": "relative/path"}):
            with self.subTest(value=value):
                self.config.write_text(json.dumps(value), encoding="utf-8")
                result = self.run_script(READER_PATH, "--config", str(self.config),
                                         "status", expected=1)
                self.assertEqual(result["error"]["code"], "configuration_unavailable")
                self.assertNotIn(PRIVATE_MARKER, json.dumps(result))

    def test_explicit_temporary_install_for_both_clients_and_no_overwrite(self):
        for client in ("codex", "claude"):
            with self.subTest(client=client):
                destination = self.directory / (client + "-skill")
                result = self.run_script(ROOT / "scripts/install_skill.py", "--client", client,
                                         "--destination", str(destination))
                self.assertTrue(result["installed"])
                self.assertFalse(result["noteContentRead"])
                self.assertFalse(result["mcpConfigurationChanged"])
                self.assertTrue((destination / "SKILL.md").is_file())
                self.assertTrue((destination / "scripts/read_snapshot.py").is_file())
                sentinel = destination / "do-not-overwrite.txt"
                sentinel.write_text("existing user file", encoding="utf-8")
                self.run_script(ROOT / "scripts/install_skill.py", "--client", client,
                                "--destination", str(destination), expected=1)
                self.assertEqual(sentinel.read_text(), "existing user file")
                self.assertFalse(self.config.exists())

    def test_installer_refuses_symlink_destination(self):
        target = self.directory / "other-directory"
        target.mkdir()
        destination = self.directory / "alias"
        destination.symlink_to(target, target_is_directory=True)
        self.run_script(ROOT / "scripts/install_skill.py", "--client", "codex",
                        "--destination", str(destination), expected=1)
        self.assertEqual(list(target.iterdir()), [])


class SwiftContractTests(unittest.TestCase):
    def test_real_swift_encoder_fixtures(self):
        directory = os.environ.get("SPARK_SWIFT_CONTRACT_FIXTURES",
                                   str(ROOT / "tests/fixtures/ios_codec"))
        reader = load_module()
        for name, count, enabled, scope in (("all", 2, True, "all"),
                                           ("favorites", 1, True, "favorites"),
                                           ("empty", 0, True, "favorites"),
                                           ("disabled", 0, False, "favorites")):
            with self.subTest(fixture=name):
                result = reader.read_snapshot(str(Path(directory) / (name + ".json")),
                                              metadata_probe=lambda _: None)
                data = result["snapshot"]
                self.assertEqual(len(data["ideas"]), count)
                self.assertEqual(data["isEnabled"], enabled)
                self.assertEqual(data["scope"], scope)
                self.assertEqual(data["schemaVersion"], 1)


if __name__ == "__main__":
    unittest.main()
