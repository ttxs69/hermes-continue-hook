"""Plugin register() smoke tests with a mock ctx — proves hooks/commands wire
up and the turn-end pipeline works end-to-end without a real gateway.

The plugin's state now lives in the shared decision service (``hookctl``),
which resolves its paths per call — so these tests point HOME at a temporary
directory and get a clean state file per test.

Runs via `python tests/test_register.py` or `python -m unittest discover -s tests`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "continue_hook_plugin", REPO / "__init__.py")
PLUGIN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PLUGIN)


class MockCtx:
    def __init__(self):
        self.hooks = {}
        self.commands = {}
        self.injected = []
        self.rejected = []
        self.mode = "cli"

    def register_hook(self, event, cb):
        self.hooks[event] = cb

    def register_command(self, name, handler, description=""):
        self.commands[name] = handler

    def inject_message(self, content, role="user", **kw):
        """Mirror hermes_cli/plugins_injection.py, including its refusals.

        The old mock accepted anything, which is how "gateway injection needs
        a session_key or an origin" stayed invisible for four rounds.
        """
        key = kw.get("session_key")
        origin = kw.get("origin")
        if self.mode == "cli":
            if origin is not None:
                self.rejected.append("cli must not receive an origin")
                return False
            self.injected.append({"content": content, "role": role, **kw})
            return True
        if key and origin is not None:
            self.rejected.append("session_key and origin are exclusive")
            return False
        if not key and origin is None:
            self.rejected.append("gateway requires session_key or origin")
            return False
        if origin is not None and not isinstance(origin, dict):
            self.rejected.append("origin must be a mapping")
            return False
        if key is not None and ":" not in str(key):
            self.rejected.append("gateway session_key must be composite")
            return False
        self.injected.append({"content": content, "role": role, **kw})
        return True


class TestRegister(unittest.TestCase):
    def setUp(self):
        # Isolate HOME: config, state file and event log all live under it.
        self._tmp = tempfile.TemporaryDirectory()
        self._real_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmp.name
        self.ctx = MockCtx()
        PLUGIN.register(self.ctx)
        PLUGIN._last_text.clear()

    def tearDown(self):
        if self._real_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._real_home
        self._tmp.cleanup()

    def _write_config(self, data: dict) -> None:
        path = Path(self._tmp.name) / ".hermes" / "continue_hook.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data and __import__("json").dumps(data), encoding="utf-8")

    def test_wires_two_hooks_and_two_commands(self):
        self.assertIn("transform_llm_output", self.ctx.hooks)
        self.assertIn("on_session_end", self.ctx.hooks)
        self.assertIn("ch-stats", self.ctx.commands)
        self.assertIn("ch-test", self.ctx.commands)

    def test_observer_never_rewrites(self):
        self.assertIsNone(self.ctx.hooks["transform_llm_output"]("any text"))

    def test_turn_end_nudge_injects_into_session(self):
        self.ctx.hooks["transform_llm_output"](
            response_text="要不要我继续拆解下一条？", session_id="s1",
            model="m", platform="cli")
        self.ctx.hooks["on_session_end"](platform="cli", session_id="s1")
        self.assertEqual(len(self.ctx.injected), 1)
        self.assertEqual(self.ctx.injected[0]["role"], "user")
        self.assertIn("continue-hook", self.ctx.injected[0]["content"])

    def test_a_gateway_session_is_not_told_it_was_delivered(self):
        """`on_session_end` carries no session_key and no origin.

        A messaging gateway therefore needs something we cannot supply, and the
        honest outcome is a recorded "unsupported", not a cheerful nudge record
        for a message nobody received.
        """
        self.ctx.mode = "gateway"
        self.ctx.hooks["transform_llm_output"](
            response_text="要不要我继续拆解下一条？", session_id="g1", model="m", platform="cli")
        self.ctx.hooks["on_session_end"](platform="qqbot", session_id="g1", completed=True)
        self.assertEqual(self.ctx.injected, [], "nothing may be injected without a key")
        events = pathlib.Path(os.environ["HOME"]) / ".hermes" / "continue-hook-events.jsonl"
        decisions = [json.loads(line) for line in events.read_text().splitlines() if line.strip()]
        self.assertTrue(
            any(d.get("decision") == "inject_unsupported_gateway" for d in decisions),
            decisions,
        )

    def test_a_durable_session_key_is_used_when_the_host_provides_one(self):
        self.ctx.mode = "gateway"
        self.ctx.hooks["transform_llm_output"](
            response_text="要不要我继续拆解下一条？", session_id="g2", model="m", platform="cli")
        self.ctx.hooks["on_session_end"](
            platform="qqbot", session_id="g2", completed=True,
            session_key="agent:main:qqbot:chat:42")
        self.assertEqual(len(self.ctx.injected), 1)
        self.assertEqual(self.ctx.injected[0]["session_key"], "agent:main:qqbot:chat:42")

    def test_turn_end_declarative_no_inject(self):
        self.ctx.hooks["transform_llm_output"](
            response_text="已完成，验证通过。", session_id="s2",
            model="m", platform="cli")
        self.ctx.hooks["on_session_end"](platform="cli", session_id="s2")
        self.assertEqual(self.ctx.injected, [])

    def test_interrupted_turn_skips(self):
        self.ctx.hooks["transform_llm_output"](
            response_text="要不要我继续？", session_id="s3",
            model="m", platform="cli")
        self.ctx.hooks["on_session_end"](
            session_id="s3", platform="qqbot", interrupted=True)
        self.assertEqual(self.ctx.injected, [])

    def test_second_turn_gets_a_fresh_allowance(self):
        """A turn that ended without a nudge resets the per-turn counter.

        This is the bug the shared decision service fixed: the old counter
        never reset, so "max_nudges_per_turn: 2" meant two nudges per session,
        ever.
        """
        self.ctx.hooks["transform_llm_output"](
            response_text="要不要我继续？", session_id="s4",
            model="m", platform="cli")
        self.ctx.hooks["on_session_end"](platform="cli", session_id="s4")
        self.assertEqual(len(self.ctx.injected), 1)

        # a turn that ends cleanly closes the chain
        self.ctx.hooks["transform_llm_output"](
            response_text="已完成。", session_id="s4", model="m", platform="cli")
        self.ctx.hooks["on_session_end"](platform="cli", session_id="s4")
        self.assertEqual(len(self.ctx.injected), 1)

        # the next turn may be nudged again
        self.ctx.hooks["transform_llm_output"](
            response_text="要不要我再做？", session_id="s4",
            model="m", platform="cli")
        self.ctx.hooks["on_session_end"](platform="cli", session_id="s4")
        self.assertEqual(len(self.ctx.injected), 2)

    def test_cooldown_blocks_rapid_second_nudge(self):
        self._write_config({"cooldown_seconds": 300})
        self.ctx.hooks["transform_llm_output"](
            response_text="要不要我继续？", session_id="s5",
            model="m", platform="cli")
        self.ctx.hooks["on_session_end"](platform="cli", session_id="s5")
        self.assertEqual(len(self.ctx.injected), 1)
        self.ctx.hooks["transform_llm_output"](
            response_text="要不要我再做？", session_id="s5",
            model="m", platform="cli")
        self.ctx.hooks["on_session_end"](platform="cli", session_id="s5")
        self.assertEqual(len(self.ctx.injected), 1)  # cooldown, not 2

    def test_dry_run_never_injects(self):
        self._write_config({"dry_run": True})
        self.ctx.hooks["transform_llm_output"](
            response_text="要不要我继续？", session_id="s6",
            model="m", platform="cli")
        self.ctx.hooks["on_session_end"](platform="cli", session_id="s6")
        self.assertEqual(self.ctx.injected, [])

    def test_disabled_never_injects(self):
        self._write_config({"enabled": False})
        self.ctx.hooks["transform_llm_output"](
            response_text="要不要我继续？", session_id="s7",
            model="m", platform="cli")
        self.ctx.hooks["on_session_end"](platform="cli", session_id="s7")
        self.assertEqual(self.ctx.injected, [])

    def test_event_log_records_the_evaluation(self):
        self.ctx.hooks["transform_llm_output"](
            response_text="要不要我继续？", session_id="s8",
            model="m", platform="cli")
        self.ctx.hooks["on_session_end"](platform="cli", session_id="s8")
        log = Path(self._tmp.name) / ".hermes" / "continue-hook-events.jsonl"
        self.assertTrue(log.exists())
        self.assertIn("nudge", log.read_text(encoding="utf-8"))

    def test_ch_test_command(self):
        out = self.ctx.commands["ch-test"]("要不要我继续？")
        self.assertIn("detect=True", out)
        out = self.ctx.commands["ch-test"]("done.")
        self.assertIn("detect=False", out)



    def test_hook_callbacks_accept_the_real_hermes_call_shape(self):
        """Hermes calls lifecycle hooks with keyword arguments only.

        A callback with a required positional parameter raises TypeError there;
        the hook then never fires and the plugin silently does nothing. This
        test calls the hooks exactly the way
        ``agent/turn_finalizer.py`` does.
        """
        import inspect

        for event, kwargs in (
            ("transform_llm_output", {
                "response_text": "要不要我继续？",
                "session_id": "kw1",
                "model": "m",
                "platform": "cli",
                "turn_id": "1",
            }),
            ("on_session_end", {
                "session_id": "kw2",
                "task_id": "t",
                "turn_id": "1",
                "completed": False,
                "failed": False,
                "interrupted": False,
                "turn_exit_reason": "done",
                "model": "m",
                "platform": "cli",
            }),
        ):
            callback = self.ctx.hooks[event]
            signature = inspect.signature(callback)
            required = [
                name for name, p in signature.parameters.items()
                if p.default is inspect.Parameter.empty
                and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            self.assertEqual(required, [], f"{event} has required positional parameters")
            # and it must survive the real call shape
            self.assertIsNone(callback(**kwargs))

if __name__ == "__main__":
    unittest.main()
