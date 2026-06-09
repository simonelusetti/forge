from __future__ import annotations

import atexit
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha1
import json
import logging
import os
from pathlib import Path
import typing as tp
import uuid
from textwrap import indent

from omegaconf import DictConfig, OmegaConf

log = logging.getLogger("forge")

def _mark_failed_on_exit(meta_path: Path) -> None:
    """atexit handler: if the run is still 'running' at process exit, mark it 'failed'."""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("status") == "running":
            meta["status"] = "failed"
            meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass  # best-effort; don't let a handler crash obscure the original error


@dataclass(frozen=True)
class Experiment:
    signature: str
    tags: list[str]
    config: DictConfig
    path: Path


@dataclass(frozen=True)
class ExperimentRun:
    experiment: Experiment
    signature: str
    tags: list[str]
    launched_on: str
    config: DictConfig
    path: Path
    finished_on: str | None = None
    metrics: dict[str, float] | None = None
    status: str = "running"  # "running" | "done" | "failed"

    def push_log(self, values: dict[str, float], *, step: int | None = None) -> None:
        entry: dict[str, tp.Any] = {"t": datetime.now(timezone.utc).isoformat(), "values": values}
        if step is not None:
            entry["step"] = step
        with (self.path / "logs.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def finish(self, metrics: dict[str, float] | None = None) -> ExperimentRun:
        finished_on = datetime.now(timezone.utc).isoformat()
        meta = json.loads((self.path / "meta.json").read_text(encoding="utf-8"))
        meta["finished_on"] = finished_on
        meta["status"] = "done"
        (self.path / "meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8"
        )
        if metrics is not None:
            (self.path / "metrics.json").write_text(
                json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8"
            )
        return replace(self, finished_on=finished_on, metrics=metrics, status="done")


@dataclass(frozen=True)
class Selection:
    experiment: Experiment
    runs: list[ExperimentRun] | None


def flatten_config(cfg: DictConfig) -> list[tuple[str, tp.Any]]:
    container = OmegaConf.to_container(cfg, resolve=True)

    def _walk(obj: tp.Any, prefix: str = "") -> tp.Iterator[tuple[str, tp.Any]]:
        if isinstance(obj, dict):
            for key in sorted(obj.keys(), key=str):
                path = f"{prefix}.{key}" if prefix else str(key)
                yield from _walk(obj[key], path)
        elif isinstance(obj, list):
            for index, value in enumerate(obj):
                path = f"{prefix}.{index}" if prefix else str(index)
                yield from _walk(value, path)
        elif prefix:
            yield prefix, obj

    return list(_walk(container)) if isinstance(container, (dict, list)) else []


def forge_exclude(cfg: DictConfig) -> set[str]:
    """Return the set of config keys to exclude for experiment identity."""
    forge = OmegaConf.select(cfg, "forge")
    base = OmegaConf.create(
        {"forge": OmegaConf.to_container(forge, resolve=True)} if forge else {}
    )
    for key in list(OmegaConf.select(cfg, "forge.exclude", default=[])):
        OmegaConf.update(base, key, None, merge=True)
    return {k for k, _ in flatten_config(base)}


def canonical_config(cfg: DictConfig, exclude: set[str] | None = None) -> list[tuple[str, tp.Any]]:
    """Flatten *cfg* and remove any keys present in *exclude*."""
    items = [(k, v) for k, v in flatten_config(cfg) if k != "run"]
    return [(k, v) for k, v in items if k not in exclude] if exclude else items


def canonical_signature(cfg: DictConfig) -> str:
    return sha1(
        json.dumps(canonical_config(cfg, forge_exclude(cfg)), sort_keys=True, default=str).encode()
    ).hexdigest()[:8]



class ExperimentStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else Path.cwd() / "outputs"
        self.xps_dir = self.root / "xps"

    def start_run(self, cfg: DictConfig, *, verbose: bool = True) -> ExperimentRun:
        signature = canonical_signature(cfg)
        tags = list(OmegaConf.select(cfg, "forge.tags", default=[]) or [])
        runtime_cfg = OmegaConf.select(cfg, "runtime") or OmegaConf.create({})

        exp_dir = (self.xps_dir / signature).resolve()
        exp_dir.mkdir(parents=True, exist_ok=True)

        run_signature = str(uuid.uuid4())[:8]
        run_dir = (exp_dir / run_signature).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        launched_on = datetime.now(timezone.utc).isoformat()

        OmegaConf.save(cfg, exp_dir / "config.yaml")
        OmegaConf.save(runtime_cfg, run_dir / "runtime.yaml")
        meta_path = run_dir / "meta.json"
        meta_path.write_text(
            json.dumps({"launched_on": launched_on, "finished_on": None, "status": "running"},
                       indent=2, sort_keys=True),
            encoding="utf-8",
        )
        atexit.register(_mark_failed_on_exit, meta_path)
        os.chdir(run_dir)

        experiment = Experiment(signature=signature, tags=tags, config=cfg, path=exp_dir)
        run = ExperimentRun(
            experiment=experiment,
            signature=f"{signature}/{run_signature}",
            tags=tags,
            launched_on=launched_on,
            config=runtime_cfg,
            path=run_dir,
            status="running",
        )
        
        logging.basicConfig(
            level=logging.INFO,
            format=f"{experiment.signature}/{run_signature} - %(asctime)s - %(levelname)s - %(message)s"
        )

        if verbose:
            log.info(f"run: {experiment.signature}/{run_signature} launched={launched_on}")
            log.info("xp config:")
            log.info(indent(OmegaConf.to_yaml(experiment.config, resolve=True).rstrip(), "    "))
            log.info("run config:")
            log.info(indent(OmegaConf.to_yaml(run.config, resolve=True).rstrip(), "    "))

        return run

    def all_selections(self) -> list[Selection]:
        experiments: dict[str, Experiment] = {}
        runs: dict[str, list[ExperimentRun]] = {}
        for run in self.list_runs():
            sig = run.experiment.signature
            experiments[sig] = run.experiment
            runs.setdefault(sig, []).append(run)
        return [Selection(exp, runs[sig]) for sig, exp in experiments.items()]

    def list_runs(self, signature: str | None = None) -> list[ExperimentRun]:
        if not self.xps_dir.exists():
            return []
        pattern = f"{signature}/*/meta.json" if signature else "*/*/meta.json"
        return [
            self.load_run(*p.relative_to(self.xps_dir).parts[:2])
            for p in sorted(self.xps_dir.glob(pattern))
        ]

    def load_run(self, signature: str, run_signature: str) -> ExperimentRun:
        exp_dir = (self.xps_dir / signature).resolve()
        run_dir = (exp_dir / run_signature).resolve()

        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        experiment_config = OmegaConf.load(exp_dir / "config.yaml")
        runtime_config = OmegaConf.load(run_dir / "runtime.yaml")
        metrics_file = run_dir / "metrics.json"
        metrics = json.loads(metrics_file.read_text(encoding="utf-8")) if metrics_file.exists() else None

        experiment = Experiment(
            signature=signature,
            tags=list(OmegaConf.select(experiment_config, "forge.tags", default=[]) or []),
            config=experiment_config,
            path=exp_dir,
        )
        
        return ExperimentRun(
            experiment=experiment,
            signature=f"{signature}/{run_signature}",
            tags=experiment.tags,
            launched_on=meta["launched_on"],
            finished_on=meta["finished_on"],
            config=runtime_config,
            path=run_dir,
            metrics=metrics,
            status=meta["status"],
        )


def start_run(cfg: DictConfig, *, store: ExperimentStore | None = None) -> ExperimentRun:
    store_root = OmegaConf.select(cfg, "forge.store", default=None)
    verbose = bool(OmegaConf.select(cfg, "forge.verbose", default=True))
    return (store or ExperimentStore(root=store_root)).start_run(cfg, verbose=verbose)
