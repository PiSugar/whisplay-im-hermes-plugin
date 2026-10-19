"""Load the plugin against the selected, real Hermes source tree."""
import importlib.util
import os
from pathlib import Path
import sys

HERMES = Path(os.environ.get("HERMES_AGENT_DIR", Path.home() / ".hermes" / "hermes-agent"))
if str(HERMES) not in sys.path:
    sys.path.insert(0, str(HERMES))

ADAPTER = Path(__file__).resolve().parents[1] / "adapter.py"


def load_adapter():
    spec = importlib.util.spec_from_file_location("whisplay_im_adapter_under_test", ADAPTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
