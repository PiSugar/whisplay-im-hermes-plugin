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

    def test_transient_poll_disconnect_keeps_retry_loop_running(self) -> None:
        adapter_class = self._adapter_class()
        poll_loop = next(
            node
            for node in adapter_class.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_poll_loop"
        )
        statements = list(ast.walk(poll_loop))
        mark_calls = [
            node
            for node in statements
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_mark_disconnected"
        ]
        self.assertTrue(mark_calls, "poll loop must report transient disconnects")
        last_mark_line = max(node.lineno for node in mark_calls)
        retry_rearms = [
            node
            for node in statements
            if isinstance(node, ast.Assign)
            and node.lineno > last_mark_line
            and any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == "_running"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and node.value.value is True
        ]
        self.assertTrue(
            retry_rearms,
            "_mark_disconnected() clears _running; transient failures must re-arm it",
        )


if __name__ == "__main__":
    unittest.main()
