from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from omegaconf import OmegaConf

from forge.cli import _grid_command
from forge.commands import GridRunError, grid
from forge.core import canonical_signature


class RuntimeSignatureTest(unittest.TestCase):
    def test_runtime_and_run_keys_do_not_change_experiment_signature(self):
        first = OmegaConf.create({"train": {"epochs": 1}, "runtime": {"seed": 1},
                                  "run": {"host": "a"}})
        second = OmegaConf.create({"train": {"epochs": 1}, "runtime": {"seed": 2},
                                   "run": {"host": "b"}})
        changed_experiment = OmegaConf.create({"train": {"epochs": 2},
                                               "runtime": {"seed": 1}})
        self.assertEqual(canonical_signature(first), canonical_signature(second))
        self.assertNotEqual(canonical_signature(first),
                            canonical_signature(changed_experiment))


class GridFailureTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="forge-grid-test-", dir="/tmp")
        self.root = Path(self.scratch.name)
        (self.root / "conf").mkdir()
        (self.root / "conf" / "config.yaml").write_text(
            "mode: ok\nforge:\n  verbose: false\n  store: "
            f"{self.root / 'custom_outputs'}\n")
        (self.root / "train.py").write_text(
            "from forge import start_run\n"
            "def main(cfg):\n"
            "    if cfg.mode == 'pre':\n"
            "        raise RuntimeError('before registration')\n"
            "    if cfg.mode == 'exit':\n"
            "        return 7\n"
            "    run = start_run(cfg)\n"
            "    if cfg.mode == 'post':\n"
            "        raise ValueError('after registration')\n"
            "    run.finish({'ok': 1})\n"
            "    return 0\n")
        self.previous_cwd = Path.cwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.scratch.cleanup()

    def test_grid_reports_failures_and_continues(self):
        with self.assertRaises(GridRunError) as caught:
            grid(None, [], [["mode=pre"], ["mode=post"], ["mode=exit"],
                            ["mode=ok"]], {})
        error = caught.exception
        self.assertEqual([overrides for overrides, _ in error.failures],
                         [["mode=pre"], ["mode=post"], ["mode=exit"]])
        self.assertEqual(sorted(run.status for run in error.runs), ["done", "failed"])
        self.assertEqual(len(list((self.root / "custom_outputs" / "xps").glob("*/*/meta.json"))), 2)

    def test_cli_returns_failure_and_prints_error(self):
        args = SimpleNamespace(
            globals=[], direct=[["mode=pre"], ["mode=ok"]], sweeps=None,
            grid_file=None, package=None, main_module="train", config_dir=None,
            config_name="config", long=False, sort=None, columns=None)
        stderr = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            status = _grid_command(args)
        self.assertEqual(status, 1)
        self.assertIn("mode=pre", stderr.getvalue())
        self.assertIn("before registration", stderr.getvalue())
        self.assertIn("ok", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
