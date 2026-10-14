"""Fixed-location discovery tests using an injected, wholly synthetic home."""
import builtins
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid

from test_snapshot_reader import ROOT, READER_PATH, PRIVATE_MARKER, record, snapshot, load_module


SOURCE_A = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
SOURCE_B = "BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB"
CONFIGURE_PATH = ROOT / "skills/spark-ideas/scripts/configure_snapshot.py"


class DiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = load_module()
        cls.discovery = sys.modules["discovery"]
        spec = importlib.util.spec_from_file_location("spark_configure_discovery_test", CONFIGURE_PATH)
        cls.configure = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"read_snapshot": cls.reader}):
            spec.loader.exec_module(cls.configure)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="spark-discovery-test-")
        self.home = Path(self.temp.name) / "synthetic-home"
        self.home.mkdir()
        self.root = self.home / "Library/Mobile Documents/iCloud~com~jinhan~Spark/Documents/Agents"
        self.root.mkdir(parents=True)
        self.config = self.home / ".config/spark-ideas/config.json"

    def tearDown(self):
        self.temp.cleanup()

    def source(self, identifier=SOURCE_A, content=None):
        directory = self.root / identifier
        directory.mkdir()
        file = directory / "ideas.json"
        value = snapshot([record(body=PRIVATE_MARKER)], sourceInstallationID=identifier)
        file.write_text(json.dumps(value if content is None else content), encoding="utf-8")
        return file

    def cli(self, *arguments, expected=0, script=READER_PATH):
        module = self.reader if script == READER_PATH else self.configure
        output = io.StringIO()
        error = io.StringIO()
        with mock.patch.object(self.discovery, "default_root", return_value=self.root), \
             mock.patch.object(self.reader, "default_root", return_value=self.root), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = module.main(["--config", str(self.config), *arguments])
        self.assertEqual(code, expected, output.getvalue() + error.getvalue())
        self.assertEqual(error.getvalue(), "")
        return json.loads(output.getvalue())

    def save_config(self, selected):
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({"snapshot": str(selected)}), encoding="utf-8")

    def test_default_root_is_only_the_fixed_spark_container(self):
        with mock.patch.object(self.discovery.Path, "home", return_value=self.home):
            self.assertEqual(self.discovery.default_root(), self.root)

    def test_single_source_discovery_reads_no_JSON_body(self):
        selected = self.source()
        def forbidden(*_, **__):
            raise AssertionError("Discovery attempted to read note data")
        with mock.patch.object(builtins, "open", side_effect=forbidden), \
             mock.patch.object(Path, "read_text", side_effect=forbidden), \
             mock.patch.object(Path, "read_bytes", side_effect=forbidden), \
             mock.patch.object(os, "read", side_effect=forbidden):
            result = self.discovery.discover_snapshot(root=self.root)
        self.assertEqual(result["path"], str(selected))
        self.assertEqual(result["sourceID"], SOURCE_A)
        self.assertIn("modifiedAt", result)
        self.assertNotIn(PRIVATE_MARKER, json.dumps(result))

    def test_discovery_does_not_parse_corrupt_JSON(self):
        selected = self.source()
        selected.write_text("corrupt JSON containing " + PRIVATE_MARKER, encoding="utf-8")
        self.assertEqual(self.discovery.discover_snapshot(root=self.root)["path"], str(selected))
        self.cli("status", expected=1)

    def test_zero_sources_and_absent_root_fail_without_scanning_elsewhere(self):
        other = self.home / "unrelated-iCloud-container" / SOURCE_A
        other.mkdir(parents=True)
        (other / "ideas.json").write_text(json.dumps(snapshot()), encoding="utf-8")
        with self.assertRaises(self.discovery.DiscoveryError):
            self.discovery.discover_snapshot(root=self.root)
        self.root.rmdir()
        with self.assertRaises(self.discovery.DiscoveryError):
            self.discovery.discover_snapshot(root=self.root)
        result = self.cli("status", expected=1)
        self.assertNotIn(str(self.home), json.dumps(result))

    def test_multiple_sources_never_auto_choose_latest(self):
        first = self.source(SOURCE_A)
        second = self.source(SOURCE_B)
        os.utime(first, (100, 100))
        os.utime(second, (200, 200))
        with self.assertRaises(self.discovery.DiscoveryError) as raised:
            self.discovery.discover_snapshot(root=self.root)
        alternatives = raised.exception.sources
        self.assertIsInstance(alternatives, list)
        self.assertEqual({item["sourceID"] for item in alternatives}, {SOURCE_A, SOURCE_B})
        for item in alternatives:
            self.assertEqual(set(item), {"sourceID", "modifiedAt"})
        result = self.cli("status", expected=1)
        self.assertNotIn(PRIVATE_MARKER, json.dumps(result))
        self.assertNotIn(str(self.home), json.dumps(result))

    def test_source_argument_selects_one_UUID_explicitly(self):
        self.source(SOURCE_A)
        second = self.source(SOURCE_B)
        result = self.discovery.discover_snapshot(root=self.root, source=SOURCE_B.lower())
        self.assertEqual(result["path"], str(second))
        status = self.cli("--source", SOURCE_B.lower(), "status")
        self.assertEqual(status["snapshot"]["sourceInstallationID"].upper(), SOURCE_B)
        self.assertNotIn(PRIVATE_MARKER, json.dumps(status))

    def test_source_argument_is_not_a_path_and_missing_source_fails(self):
        self.source()
        for source in ("../outside", "/absolute/path", "not-a-uuid", SOURCE_B):
            with self.subTest(source=source):
                with self.assertRaises(self.discovery.DiscoveryError):
                    self.discovery.discover_snapshot(root=self.root, source=source)
                result = self.cli("--source", source, "status", expected=1)
                self.assertNotIn(PRIVATE_MARKER, json.dumps(result))

    def test_ignored_names_nested_folders_and_other_filenames(self):
        selected = self.source()
        bad = self.root / "not-a-UUID"
        bad.mkdir()
        (bad / "ideas.json").write_text(PRIVATE_MARKER, encoding="utf-8")
        (self.root / "ideas.json").write_text(PRIVATE_MARKER, encoding="utf-8")
        nested = self.root / SOURCE_B / "nested"
        nested.mkdir(parents=True)
        (nested / "ideas.json").write_text(PRIVATE_MARKER, encoding="utf-8")
        (nested.parent / "ideas-backup.json").write_text(PRIVATE_MARKER, encoding="utf-8")
        self.assertEqual(self.discovery.discover_snapshot(root=self.root)["path"], str(selected))

    def test_directory_fifo_and_symlink_leaf_are_never_candidates(self):
        selected = self.source()
        selected.unlink()
        selected.mkdir()
        with self.assertRaises(self.discovery.DiscoveryError):
            self.discovery.discover_snapshot(root=self.root)
        selected.rmdir()
        os.mkfifo(selected)
        with self.assertRaises(self.discovery.DiscoveryError):
            self.discovery.discover_snapshot(root=self.root)
        selected.unlink()
        external = self.home / "external.json"
        external.write_text(json.dumps(snapshot()), encoding="utf-8")
        selected.symlink_to(external)
        with self.assertRaises(self.discovery.DiscoveryError):
            self.discovery.discover_snapshot(root=self.root)

    def test_symlink_root_source_and_parent_are_rejected(self):
        selected = self.source()
        source_directory = selected.parent
        moved_source = self.home / "moved-source"
        source_directory.rename(moved_source)
        source_directory.symlink_to(moved_source, target_is_directory=True)
        with self.assertRaises(self.discovery.DiscoveryError):
            self.discovery.discover_snapshot(root=self.root)
        source_directory.unlink()
        moved_source.rename(source_directory)
        moved_root = self.home / "moved-root"
        self.root.rename(moved_root)
        self.root.symlink_to(moved_root, target_is_directory=True)
        with self.assertRaises(self.discovery.DiscoveryError):
            self.discovery.discover_snapshot(root=self.root)
        self.root.unlink()
        moved_root.rename(self.root)
        documents = self.root.parent
        moved_documents = self.home / "moved-documents"
        documents.rename(moved_documents)
        documents.symlink_to(moved_documents, target_is_directory=True)
        with self.assertRaises(self.discovery.DiscoveryError):
            self.discovery.discover_snapshot(root=self.root)

    def test_source_limit_is_bounded_and_fail_closed(self):
        for index in range(64):
            self.source(str(uuid.UUID(int=index + 1)).upper())
        with self.assertRaises(self.discovery.DiscoveryError) as raised:
            self.discovery.discover_snapshot(root=self.root)
        self.assertEqual(len(raised.exception.sources), 64)
        self.source(str(uuid.UUID(int=65)).upper())
        with self.assertRaises(self.discovery.DiscoveryError) as raised:
            self.discovery.discover_snapshot(root=self.root)
        self.assertNotIn(PRIVATE_MARKER, str(raised.exception))
        # The over-limit case must not return a truncated source list for selection.
        self.assertNotEqual(len(raised.exception.sources or []), 64)

    def test_irrelevant_directory_entries_are_also_bounded(self):
        self.source()
        for index in range(257):
            (self.root / ("ignored-entry-" + str(index))).touch()
        with self.assertRaises(self.discovery.DiscoveryError) as raised:
            self.discovery.discover_snapshot(root=self.root)
        self.assertIsNone(raised.exception.sources)

    def test_source_directory_swap_during_discovery_fails_closed(self):
        selected = self.source()
        original_stat = self.discovery.os.stat
        changed = False
        def swap_after_metadata(path, *arguments, **keywords):
            nonlocal changed
            result = original_stat(path, *arguments, **keywords)
            if path == SOURCE_A and not changed:
                changed = True
                selected.parent.rename(self.home / "moved-source")
                selected.parent.symlink_to(self.home / "moved-source", target_is_directory=True)
            return result
        with mock.patch.object(self.discovery.os, "stat", side_effect=swap_after_metadata):
            with self.assertRaises(self.discovery.DiscoveryError):
                self.discovery.discover_snapshot(root=self.root)

    def test_explicit_snapshot_never_discovers_other_sources(self):
        self.source(SOURCE_A)
        self.source(SOURCE_B)
        explicit = self.home / "authorized-explicit.json"
        explicit.write_text(json.dumps(snapshot()), encoding="utf-8")
        status = self.cli("--snapshot", str(explicit), "status")
        self.assertTrue(status["available"])
        self.assertNotIn(PRIVATE_MARKER, json.dumps(status))

    def test_missing_config_auto_discovers_without_saving_setting(self):
        self.source()
        result = self.cli("status")
        self.assertTrue(result["available"])
        self.assertFalse(self.config.exists())
        self.assertNotIn(PRIVATE_MARKER, json.dumps(result))

    def test_existing_missing_or_invalid_config_never_changes_source(self):
        self.source()
        missing = self.home / "previously-selected-missing.json"
        self.save_config(missing)
        before = self.config.read_bytes()
        self.cli("status", expected=1)
        self.assertEqual(self.config.read_bytes(), before)
        self.config.write_text("corrupt local configuration", encoding="utf-8")
        self.cli("status", expected=1)
        self.assertEqual(self.config.read_text(), "corrupt local configuration")

    def test_symlink_or_directory_config_never_triggers_discovery(self):
        self.source()
        self.config.parent.mkdir(parents=True)
        self.config.symlink_to(self.home / "missing-setting-target.json")
        result = self.cli("status", expected=1)
        self.assertEqual(result["error"]["code"], "configuration_unavailable")
        self.assertTrue(self.config.is_symlink())
        self.config.unlink()
        self.config.mkdir()
        result = self.cli("status", expected=1)
        self.assertEqual(result["error"]["code"], "configuration_unavailable")
        self.assertTrue(self.config.is_dir())

    def test_existing_selection_is_not_overridden_by_source_flag(self):
        first = self.source(SOURCE_A)
        self.source(SOURCE_B)
        self.save_config(first)
        before = self.config.read_bytes()
        self.cli("--source", SOURCE_B, "status", expected=1)
        self.assertEqual(self.config.read_bytes(), before)
        result = self.cli("status")
        self.assertEqual(result["snapshot"]["sourceInstallationID"].upper(), SOURCE_A)

    def test_auto_configure_no_body_read_and_no_existing_setting_overwrite(self):
        selected = self.source()
        selected.write_text("corrupt JSON " + PRIVATE_MARKER, encoding="utf-8")
        result = self.cli("--auto", script=CONFIGURE_PATH)
        self.assertTrue(result["configured"])
        self.assertFalse(result["noteContentRead"])
        self.assertNotIn(PRIVATE_MARKER, json.dumps(result))
        self.assertEqual(json.loads(self.config.read_text()), {"snapshot": str(selected)})
        before = self.config.read_bytes()
        self.cli("--auto", script=CONFIGURE_PATH, expected=1)
        self.assertEqual(self.config.read_bytes(), before)

    def test_auto_configure_multiple_sources_requires_explicit_source(self):
        self.source(SOURCE_A)
        second = self.source(SOURCE_B)
        self.cli("--auto", script=CONFIGURE_PATH, expected=1)
        self.assertFalse(self.config.exists())
        self.cli("--auto", "--source", SOURCE_B, script=CONFIGURE_PATH)
        self.assertEqual(json.loads(self.config.read_text()), {"snapshot": str(second)})

    def test_auto_source_ID_mismatch_fails_but_explicit_file_keeps_contract(self):
        selected = self.source(SOURCE_A, snapshot(sourceInstallationID=SOURCE_B))
        self.cli("status", expected=1)
        status = self.cli("--snapshot", str(selected), "status")
        self.assertTrue(status["available"])

    def test_pinned_auto_source_still_checks_source_identity(self):
        selected = self.source()
        self.cli("--auto", script=CONFIGURE_PATH)
        selected.write_text(json.dumps(snapshot(sourceInstallationID=SOURCE_B)), encoding="utf-8")
        result = self.cli("status", expected=1)
        self.assertEqual(result["error"]["code"], "snapshot_invalid")
        self.assertEqual(json.loads(self.config.read_text()), {"snapshot": str(selected)})

    def test_snapshot_and_source_flags_are_mutually_exclusive(self):
        selected = self.source()
        self.cli("--snapshot", str(selected), "--source", SOURCE_A, "status", expected=1)


if __name__ == "__main__":
    unittest.main()
