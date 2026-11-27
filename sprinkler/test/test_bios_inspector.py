"""Unit tests for sprinkler.checker.bios (whitelist / inspector / path formatting)."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sprinkler.arguments import get_args_parser
from sprinkler.checker.bios import (
    DEFAULT_WHITELIST_PATH,
    BiosInspectError,
    BiosInspector,
    build_rows,
    format_menu_path,
    load_whitelist,
)
from sprinkler.checker.bios.inspector import normalize_registry_version
from sprinkler.checker.bios.runner import (
    _console_row,
    _target_creds,
    prompt_missing_passwords,
    split_keywords,
    validate_target_options,
)
from sprinkler.connect.targets import ConnectionTarget


def _write_whitelist(directory: str, data: dict) -> str:
    path = Path(directory) / "whitelist.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _sample_whitelist(directory: str) -> str:
    data = {
        "categories": [
            {
                "name": "算力",
                "description": "test",
                "attributes": [
                    {
                        "name": "PowerPolicy",
                        "aliases": ["PowerPolicy", "CustomPowerPolicy"],
                        "menu_path": "Advanced/Performance Config",
                        "description": "power policy",
                    },
                    {
                        "name": "SMT2En",
                        "aliases": ["SMT2En", "SMT2"],
                        "menu_path": "Advanced/Power And Performance Configuration/CPU PM Control",
                        "description": "smt",
                    },
                    {
                        "name": "NotExposed",
                        "aliases": ["NotExposed"],
                        "description": "missing on this model",
                    },
                ],
            }
        ]
    }
    return _write_whitelist(directory, data)


class FormatMenuPathTest(unittest.TestCase):
    def test_slash_to_arrow(self):
        self.assertEqual(
            format_menu_path("./Advanced/Socket Configuration/Advanced Power Mgmt. Configuration",
                             "Power Policy"),
            "Advanced->Socket Configuration->Advanced Power Mgmt. Configuration->Power Policy",
        )

    def test_backslash_and_empty(self):
        self.assertEqual(format_menu_path("Advanced\\MISC Config", "Support Smmu"),
                         "Advanced->MISC Config->Support Smmu")
        self.assertEqual(format_menu_path("", "Power Policy"), "Power Policy")

    def test_io_slash_in_component_is_preserved(self):
        self.assertEqual(
            format_menu_path(
                "./Advanced/Socket Configuration/IIO Configuration/"
                "Intel(R) VT for Directed I/O(VT-d)",
                "VT-d",
            ),
            "Advanced->Socket Configuration->IIO Configuration->"
            "Intel(R) VT for Directed I/O(VT-d)->VT-d",
        )


class RegistryVersionTest(unittest.TestCase):
    def test_dot_and_underscore(self):
        self.assertEqual(normalize_registry_version("BiosAttributeRegistry.1.5.3"), (1, 5, 3))
        self.assertEqual(normalize_registry_version("BiosAttributeRegistry.v1_5_3"), (1, 5, 3))
        self.assertIsNone(normalize_registry_version("OtherRegistry"))


class _StubReader:
    """按路径返回预置 ``(data, status)`` 的假 RedfishReader。"""

    def __init__(self, responses):
        self._responses = responses
        self.requested = []

    def get(self, path):
        self.requested.append(path)
        return self._responses.get(path, (None, 404))


def _registry_responses(members, registry_uri, file_uri):
    return {
        "/redfish/v1/Registries": (
            {"Members": [{"@odata.id": uri} for uri in members]}, 200),
        registry_uri: (
            {"Location": [{"Language": "en", "Uri": file_uri}]}, 200),
        file_uri: (
            {"RegistryEntries": {"Attributes": [
                {"AttributeName": "A", "DisplayName": "A Name",
                 "MenuPath": "./Advanced/A"},
            ]}}, 200),
    }


class RegistryFallbackTest(unittest.TestCase):
    def test_version_exact_match_wins(self):
        members = [
            "/redfish/v1/Registries/BiosAttributeRegistry/BiosAttributeRegistry.1.4.0",
            "/redfish/v1/Registries/BiosAttributeRegistry/BiosAttributeRegistry.1.5.3",
        ]
        file_uri = members[1] + ".json"
        reader = _StubReader(_registry_responses(members, members[1], file_uri))
        meta = BiosInspector(reader)._load_registry("BiosAttributeRegistry.1.5.3")
        self.assertEqual(meta["A"]["display_name"], "A Name")
        self.assertEqual(meta["A"]["menu_path"], "./Advanced/A")
        self.assertNotIn(members[0], reader.requested)

    def test_unversioned_member_falls_back(self):
        registry_uri = "/redfish/v1/Registries/BiosAttributeRegistry"
        file_uri = "/redfish/v1/Registries/BiosAttributeRegistry.1.76.0.json"
        reader = _StubReader(_registry_responses([registry_uri], registry_uri, file_uri))
        meta = BiosInspector(reader)._load_registry("BiosAttributeRegistry.1.76")
        self.assertEqual(meta["A"]["display_name"], "A Name")
        self.assertIn(registry_uri, reader.requested)

    def test_version_mismatch_still_raises(self):
        registry_uri = "/redfish/v1/Registries/BiosAttributeRegistry/BiosAttributeRegistry.1.4.0"
        reader = _StubReader(_registry_responses([registry_uri], registry_uri, "/x.json"))
        with self.assertRaises(BiosInspectError):
            BiosInspector(reader)._load_registry("BiosAttributeRegistry.1.5.3")
        self.assertNotIn(registry_uri, reader.requested)

    def test_multiple_unversioned_candidates_raises(self):
        first = "/redfish/v1/Registries/BiosAttributeRegistry"
        second = "/redfish/v1/Registries/BiosAttributeRegistryBackup"
        reader = _StubReader(_registry_responses([first, second], first, "/x.json"))
        with self.assertRaises(BiosInspectError):
            BiosInspector(reader)._load_registry("BiosAttributeRegistry.1.76")


class WhitelistTest(unittest.TestCase):
    def test_duplicate_alias_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _sample_whitelist(tmp)
            load_whitelist(path)

            conflict = {
                "categories": [
                    {"name": "A", "attributes": [{"name": "X", "aliases": ["Dup"]}]},
                    {"name": "B", "attributes": [{"name": "Y", "aliases": ["dup"]}]},
                ]
            }
            with self.assertRaises(ValueError):
                load_whitelist(_write_whitelist(tmp, conflict))

    def test_default_whitelist_loads_and_covers_categories(self):
        whitelist = load_whitelist(str(DEFAULT_WHITELIST_PATH))
        names = {category.name for category in whitelist.categories}
        self.assertTrue({"性能与功耗", "Socket 配置", "内存配置", "外设与杂项"} <= names)
        self.assertGreater(len(whitelist.items), 20)


class BuildRowsTest(unittest.TestCase):
    def test_alias_match_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            whitelist = load_whitelist(_sample_whitelist(tmp))
        current = {"CustomPowerPolicy": "Performance"}
        registry_meta = {
            "CustomPowerPolicy": {
                "display_name": "Power Policy",
                "default_value": "Custom",
                "allowed_values": ["Performance", "Custom"],
                "menu_path": "./Advanced/Socket Configuration/Advanced Power Mgmt. Configuration",
                "help_text": "help",
                "read_only": False,
                "hidden": False,
            }
        }
        rows, missing = build_rows(whitelist, current, registry_meta)
        by_name = {row["Attribute"]: row for row in rows}

        self.assertEqual(list(by_name), ["PowerPolicy"])
        self.assertEqual(by_name["PowerPolicy"]["MatchedAttribute"], "CustomPowerPolicy")
        self.assertEqual(by_name["PowerPolicy"]["CurrentValue"], "Performance")
        self.assertEqual(
            by_name["PowerPolicy"]["FullMenuPath"],
            "Advanced->Socket Configuration->Advanced Power Mgmt. Configuration->Power Policy",
        )
        self.assertNotIn("Verdict", by_name["PowerPolicy"])
        self.assertNotIn("ExpectedValue", by_name["PowerPolicy"])

        self.assertIn("NotExposed", missing)
        self.assertIn("SMT2En", missing)

    def test_disabled_items_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_whitelist(str(tmp), {"categories": [{
                "name": "算力",
                "attributes": [
                    {"name": "PowerPolicy", "enabled": True},
                    {"name": "SMT2En", "enabled": False},
                ],
            }]})
            whitelist = load_whitelist(path).select()[0]
        rows, missing = build_rows(
            whitelist, {"PowerPolicy": "Performance", "SMT2En": "Enabled"}, {})
        self.assertEqual([row["Attribute"] for row in rows], ["PowerPolicy"])
        self.assertNotIn("SMT2En", missing)

    def test_select_by_keyword_includes_disabled_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_whitelist(str(tmp), {"categories": [{
                "name": "算力",
                "attributes": [
                    {"name": "PowerPolicy", "enabled": True},
                    {"name": "ConfigTDP", "aliases": ["ConfigTdp"], "enabled": False},
                ],
            }]})
            whitelist, unmatched = load_whitelist(path).select(["PowerPolicy", "configtdp"])
        self.assertEqual([item.name for item in whitelist.items], ["PowerPolicy", "ConfigTDP"])
        self.assertEqual(unmatched, [])

    def test_select_reports_unmatched_keyword(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_whitelist(str(tmp), {"categories": [{
                "name": "算力",
                "attributes": [{"name": "PowerPolicy"}],
            }]})
            whitelist, unmatched = load_whitelist(path).select(["NoSuchKey"])
        self.assertEqual([item.name for item in whitelist.items], [])
        self.assertEqual(unmatched, ["NoSuchKey"])

    def test_zh_name_translated_in_console_row(self):
        row = {
            "Node": "10.0.0.9", "Category": "性能与功耗", "Attribute": "PowerPolicy",
            "ZhName": "电源性能策略", "CurrentValue": "Performance",
            "FullMenuPath": "Advanced->Performance Config",
        }
        self.assertEqual(
            _console_row(row),
            "电源性能策略 | PowerPolicy | Performance | Advanced->Performance Config")

    def test_split_keywords_dedup(self):
        self.assertEqual(split_keywords("PowerPolicy| ConfigTDP |powerpolicy|"),
                         ["PowerPolicy", "ConfigTDP"])
        self.assertEqual(split_keywords(None), [])


class TargetOptionsTest(unittest.TestCase):
    @staticmethod
    def _args(**overrides):
        values = {
            "bios_inspect": True,
            "sp": "",
            "ca": "",
            "xlsx": None,
            "node": "",
            "user": "root",
            "pwd": "",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_ip_mode_accepts_user_and_password(self):
        validate_target_options(self._args(ip="10.0.0.1", user="Administrator", pwd="pwd"))

    def test_cluster_mode_rejects_user(self):
        with self.assertRaises(ValueError):
            validate_target_options(self._args(sp="0", ca="0", user="Administrator"))

    def test_cluster_mode_rejects_password(self):
        with self.assertRaises(ValueError):
            validate_target_options(self._args(sp="0", ca="0", pwd="pwd"))

    def test_xlsx_mode_rejects_credentials(self):
        with self.assertRaises(ValueError):
            validate_target_options(self._args(xlsx="/tmp/superpod_0.xlsx", pwd="pwd"))

    def test_structured_node_rejects_credentials(self):
        with self.assertRaises(ValueError):
            validate_target_options(self._args(node="{s0:[c1:0-7]}", pwd="pwd"))

    def test_cluster_mode_without_credentials_is_allowed(self):
        validate_target_options(self._args(sp="0", ca="0", node="1-8"))

    def test_non_bios_command_is_not_validated(self):
        validate_target_options(self._args(bios_inspect=False, sp="0", pwd="pwd"))


class RedfishPortTest(unittest.TestCase):
    def test_default_port_is_443(self):
        target = ConnectionTarget(name="n", host="10.0.0.1", port=22,
                                  user="Administrator", password="pwd",
                                  meta={"source": "xlsx"})
        self.assertEqual(_target_creds(target)[3], 443)

    def test_explicit_port_is_preserved(self):
        target = ConnectionTarget(name="n", host="10.0.0.1", port=8443,
                                  user="Administrator", password="pwd",
                                  meta={"port_explicit": True, "source": "cli_ip"})
        self.assertIn("port_explicit", target.meta or {})
        self.assertEqual(_target_creds(target)[3], 8443)

    def test_node_mode_without_port_uses_443(self):
        target = ConnectionTarget(name="n", host="10.0.0.1", port=22,
                                  user="Administrator", password="pwd",
                                  meta={"port_explicit": False, "source": "cli_node_ip"})
        self.assertEqual(_target_creds(target)[3], 443)


class PasswordPromptTest(unittest.TestCase):
    @staticmethod
    def _target(host="10.0.0.1", password=""):
        return ConnectionTarget(name=host, host=host, port=22,
                                user="Administrator", password=password)

    def test_single_missing_password_prompts(self):
        with patch("sprinkler.checker.bios.runner.sys.stdin") as stdin, \
                patch("sprinkler.checker.bios.runner.getpass.getpass",
                      return_value="secret") as prompt:
            stdin.isatty.return_value = True
            targets = prompt_missing_passwords([self._target()])
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].password, "secret")
        prompt.assert_called_once()

    def test_existing_password_skips_prompt(self):
        with patch("sprinkler.checker.bios.runner.getpass.getpass") as prompt:
            targets = prompt_missing_passwords([self._target(password="saved")])
        self.assertEqual(targets[0].password, "saved")
        prompt.assert_not_called()

    def test_non_tty_does_not_prompt(self):
        with patch("sprinkler.checker.bios.runner.sys.stdin") as stdin:
            stdin.isatty.return_value = False
            with self.assertRaises(ValueError):
                prompt_missing_passwords([self._target()])

    def test_multi_targets_do_not_prompt(self):
        with patch("sprinkler.checker.bios.runner.getpass.getpass") as prompt:
            with self.assertRaises(ValueError):
                prompt_missing_passwords([self._target("10.0.0.1"),
                                          self._target("10.0.0.2")])
        prompt.assert_not_called()

    def test_empty_password_cancels(self):
        with patch("sprinkler.checker.bios.runner.sys.stdin") as stdin, \
                patch("sprinkler.checker.bios.runner.getpass.getpass", return_value=""):
            stdin.isatty.return_value = True
            with self.assertRaises(ValueError):
                prompt_missing_passwords([self._target()])


def _cli_action_help(dest: str) -> str:
    for group in get_args_parser()._action_groups:
        for action in group._group_actions:
            if action.dest == dest:
                return action.help or ""
    raise AssertionError(f"未找到参数 {dest}")


class HelpTextTest(unittest.TestCase):
    def test_bios_inspect_help_stays_concise(self):
        help_text = _cli_action_help("bios_inspect")
        # 关键信息必须在，细节（凭据语义/多示例）不应重复堆在 help 里
        self.assertIn("bios_whitelist.json", help_text)
        self.assertIn("-bios-whitelist", help_text)
        self.assertIn("-ip", help_text)
        self.assertIn("-sp/-ca/-node", help_text)
        self.assertLessEqual(len(help_text), 400)


if __name__ == "__main__":
    unittest.main()
