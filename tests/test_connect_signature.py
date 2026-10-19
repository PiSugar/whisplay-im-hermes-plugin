import ast
from pathlib import Path
import unittest


ADAPTER = Path(__file__).resolve().parents[1] / "adapter.py"


class ConnectContractTest(unittest.TestCase):
    @staticmethod
    def _adapter_class() -> ast.ClassDef:
        tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
        return next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "WhisplayIMAdapter"
        )

    def test_connect_accepts_keyword_only_is_reconnect(self) -> None:
        adapter_class = self._adapter_class()
        connect = next(
            node
            for node in adapter_class.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "connect"
        )
        keyword_only = [arg.arg for arg in connect.args.kwonlyargs]
        self.assertIn("is_reconnect", keyword_only)
        index = keyword_only.index("is_reconnect")
        default = connect.args.kw_defaults[index]
        if not isinstance(default, ast.Constant):
            self.fail("is_reconnect must have a constant default")
        self.assertIs(default.value, False)


if __name__ == "__main__":
    unittest.main()
