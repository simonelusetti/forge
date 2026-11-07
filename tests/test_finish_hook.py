import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from omegaconf import OmegaConf

from forge import start_run
from forge.core import _mark_failed_on_exit, canonical_signature


class FinishHookTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="forge-hook-test-", dir="/tmp")
        self.addCleanup(scratch.cleanup)
        self.addCleanup(os.chdir, Path.cwd())
        self.root = Path(scratch.name)
        self.cfg = OmegaConf.create({"forge": {"store": str(self.root / "outputs"),
                                               "verbose": False}})

    def test_success_and_shutdown_failure_notify_once_with_saved_results(self):
        events = self.root / "events.jsonl"
        signature = canonical_signature(self.cfg)
        self.cfg.forge.on_finish = [sys.executable, "-c",
            "import json, os, sys; from pathlib import Path\n"
            "p = Path(os.environ['FORGE_RUN_PATH'])\n"
            "assert p.is_absolute() and p == Path.cwd()\n"
            "assert json.loads((p / 'meta.json').read_text())['status'] == os.environ['FORGE_STATUS']\n"
            "with open(sys.argv[1], 'a') as f:\n"
            " f.write(json.dumps({'status': os.environ['FORGE_STATUS'], 'metrics': "
            "json.loads((p / 'metrics.json').read_text()) if (p / 'metrics.json').exists() else None}) + '\\n')",
            str(events)]
        self.assertEqual(canonical_signature(self.cfg), signature)
        config_path = self.root / "config.yaml"
        OmegaConf.save(self.cfg, config_path)
        for status in ("done", "failed"):
            child = subprocess.run([sys.executable, "-c",
                "import sys; from forge import start_run; from omegaconf import OmegaConf\n"
                "run = start_run(OmegaConf.load(sys.argv[1]))\n"
                "if sys.argv[2] == 'failed': raise RuntimeError('training failed')\n"
                "run.finish({'loss': 0.25}); run.finish({'loss': 0.25})",
                str(config_path), status], capture_output=True, text=True, timeout=30,
                env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
            self.assertEqual(child.returncode, 0 if status == "done" else 1, child.stderr)
        self.assertEqual([json.loads(line) for line in events.read_text().splitlines()],
                         [{"status": "done", "metrics": {"loss": 0.25}},
                          {"status": "failed", "metrics": None}])

    def test_command_errors_leave_run_outcome_intact(self):
        for command in ([str(self.root / "missing")],
                        [sys.executable, "-c", "raise SystemExit(7)"], "invalid command"):
            self.cfg.forge.on_finish = command
            for status in ("done", "failed"):
                with self.subTest(command=command, status=status):
                    run = start_run(self.cfg)
                    with self.assertLogs("forge", level="WARNING"):
                        if status == "done":
                            run.finish()
                        else:
                            _mark_failed_on_exit(run.path / "meta.json")
                    self.assertEqual(json.loads((run.path / "meta.json").read_text())["status"],
                                     status)
