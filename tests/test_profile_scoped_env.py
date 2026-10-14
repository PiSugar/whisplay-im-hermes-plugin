"""Regression: under a multiplexing gateway the adapter must read the profile's own .env scope.

Hermes' multiplexer loads secondary-profile secrets into a contextvar scope instead of
os.environ. A bare os.getenv() then misses WHISPLAY_IM_BASE_URL and silently falls back to
http://127.0.0.1:18888 (connection refused forever). Run with Hermes' interpreter:

    ~/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests

Set HERMES_AGENT_DIR if Hermes is installed somewhere else. The test is skipped when the
installed Hermes has no profile secret scope.
"""
import importlib.util
import os
from pathlib import Path
import sys
import unittest

HERMES = Path(os.environ.get("HERMES_AGENT_DIR", Path.home() / ".hermes" / "hermes-agent"))
if str(HERMES) not in sys.path:
    sys.path.insert(0, str(HERMES))

ADAPTER = Path(__file__).resolve().parents[1] / "adapter.py"

try:
    import agent.secret_scope  # noqa: F401
    import gateway.platforms._shared  # noqa: F401

    HAS_SECRET_SCOPE = True
except ImportError:
    HAS_SECRET_SCOPE = False


def _load_adapter():
    spec = importlib.util.spec_from_file_location("whisplay_im_adapter_under_test", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(HAS_SECRET_SCOPE, "Hermes with profile secret scopes not importable")
class ProfileScopedEnvTest(unittest.TestCase):
    SCOPED = {
        "WHISPLAY_IM_BASE_URL": "http://device.example.test:18888",
        "WHISPLAY_IM_TOKEN": "scoped-test-token",
        "WHISPLAY_IM_CHAT_ID": "scoped-chat",
    }

    def setUp(self) -> None:
        for name in self.SCOPED:
            os.environ.pop(name, None)
        from agent.secret_scope import set_multiplex_active, set_secret_scope

        set_multiplex_active(True)
        self._token = set_secret_scope(dict(self.SCOPED), profile_home="/tmp/whisplay-im-profile-test")
        self.mod = _load_adapter()

    def tearDown(self) -> None:
        from agent.secret_scope import reset_secret_scope, set_multiplex_active

        reset_secret_scope(self._token)
        set_multiplex_active(False)

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


if __name__ == "__main__":
    unittest.main()
