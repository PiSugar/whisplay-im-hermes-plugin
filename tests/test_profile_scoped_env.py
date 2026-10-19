"""Profile isolation and simulated legacy import fallback.

Use a Hermes source tree with secret-scope support (v0.21.6 or later) selected
by HERMES_AGENT_DIR. Use a disposable HERMES_HOME for the full test suite.
"""
import builtins
import os
import unittest
from unittest import mock

from support import load_adapter

from agent import secret_scope


class ProfileScopedEnvTest(unittest.TestCase):
    SCOPED = {
        "WHISPLAY_IM_BASE_URL": "http://device.example.test:18888",
        "WHISPLAY_IM_TOKEN": "scoped-test-token",
        "WHISPLAY_IM_CHAT_ID": "scoped-chat",
    }

    def setUp(self) -> None:
        # Restore removed/replaced keys too, including when setup fails.
        self.enterContext(mock.patch.dict(os.environ, {}))
        for name in self.SCOPED:
            os.environ.pop(name, None)
        # Preserve the process flag, not the OR of process and task-local modes.
        self.addCleanup(secret_scope.set_multiplex_active, secret_scope._MULTIPLEX_ACTIVE)
        secret_scope.set_multiplex_active(True)
        token = secret_scope.set_secret_scope(dict(self.SCOPED), profile_home="/test-profile")
        self.addCleanup(secret_scope.reset_secret_scope, token)
        self.mod = load_adapter()

    def test_base_url_and_token_come_from_profile_scope(self) -> None:
        self.assertEqual(self.mod._base_url_from({}), "http://device.example.test:18888")
        self.assertEqual(self.mod._token_from({}), "scoped-test-token")

    def test_env_enablement_seed_uses_profile_scope(self) -> None:
        seed = self.mod._env_enablement()
        self.assertEqual(seed["base_url"], "http://device.example.test:18888")
        self.assertEqual(seed["chat_id"], "scoped-chat")
        self.assertEqual(seed["token"], "scoped-test-token")

    def test_explicit_extra_still_wins(self) -> None:
        self.assertEqual(self.mod._base_url_from({"base_url": "http://x:1/"}), "http://x:1")
        self.assertEqual(self.mod._token_from({"token": "explicit-test-token"}), "explicit-test-token")

    def test_scope_wins_over_conflicting_process_values(self) -> None:
        os.environ.update({name: "other-profile-value" for name in self.SCOPED})
        self.assertEqual(self.mod._base_url_from({}), self.SCOPED["WHISPLAY_IM_BASE_URL"])
        self.assertEqual(self.mod._token_from({}), self.SCOPED["WHISPLAY_IM_TOKEN"])
        self.assertEqual(self.mod._env_enablement()["chat_id"], self.SCOPED["WHISPLAY_IM_CHAT_ID"])

    def test_missing_scoped_keys_do_not_inherit_process_or_previous_scope(self) -> None:
        os.environ.update({name: "other-profile-value" for name in self.SCOPED})
        token = secret_scope.set_secret_scope({}, profile_home="/empty-test-profile")
        try:
            self.assertEqual(self.mod._base_url_from({}), self.mod.DEFAULT_BASE_URL)
            self.assertEqual(self.mod._token_from({}), "")
            seed = self.mod._env_enablement()
            self.assertEqual(seed["chat_id"], self.mod.DEFAULT_CHAT_ID)
            self.assertNotIn("token", seed)
        finally:
            secret_scope.reset_secret_scope(token)
        self.assertEqual(self.mod._token_from({}), self.SCOPED["WHISPLAY_IM_TOKEN"])


class LegacyFallbackTest(unittest.TestCase):
    def test_import_fallback_uses_process_values_and_defaults(self) -> None:
        original_import = builtins.__import__

        def legacy_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "gateway.platforms._shared" and "get_scoped_secret" in fromlist:
                raise ImportError("simulated older Hermes without get_scoped_secret")
            return original_import(name, globals, locals, fromlist, level)

        with mock.patch.dict(os.environ, {
            "WHISPLAY_IM_BASE_URL": "http://legacy.example.test:18888/",
            "WHISPLAY_IM_TOKEN": "legacy-test-token",
            "WHISPLAY_IM_CHAT_ID": "legacy-chat",
        }), mock.patch("builtins.__import__", side_effect=legacy_import):
            mod = load_adapter()
            self.assertIsNone(mod._get_scoped_secret)
            self.assertEqual(mod._base_url_from({}), "http://legacy.example.test:18888")
            self.assertEqual(mod._token_from({}), "legacy-test-token")
            self.assertEqual(mod._env_enablement()["chat_id"], "legacy-chat")
            os.environ.pop("WHISPLAY_IM_TOKEN")
            self.assertEqual(mod._token_from({}), "")


class ScopeCleanupTest(unittest.TestCase):
    def test_fixture_restores_environment_scope_and_both_prior_flag_states(self) -> None:
        previous_flag = secret_scope._MULTIPLEX_ACTIVE
        try:
            for active in (False, True):
                with self.subTest(previous_multiplex=active), mock.patch.dict(os.environ, {
                    "WHISPLAY_IM_BASE_URL": "http://outer.example.test:18888",
                    "WHISPLAY_IM_TOKEN": "outer-test-token",
                    "WHISPLAY_IM_CHAT_ID": "outer-chat",
                }):
                    secret_scope.set_multiplex_active(active)
                    token = secret_scope.set_secret_scope({"OUTER": "test"}, profile_home="/outer-profile")
                    try:
                        before = dict(os.environ)
                        fixture = ProfileScopedEnvTest("test_base_url_and_token_come_from_profile_scope")
                        try:
                            fixture.setUp()
                        finally:
                            fixture.doCleanups()
                        self.assertEqual(dict(os.environ), before)
                        self.assertEqual(secret_scope._MULTIPLEX_ACTIVE, active)
                        self.assertEqual(secret_scope.current_secret_scope(), {"OUTER": "test"})
                        self.assertEqual(secret_scope.current_secret_scope_home(), "/outer-profile")
                    finally:
                        secret_scope.reset_secret_scope(token)
        finally:
            secret_scope.set_multiplex_active(previous_flag)


if __name__ == "__main__":
    unittest.main()
