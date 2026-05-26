from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from hashlib import sha1
import json
import os
from pathlib import Path
import typing as tp
import uuid

from omegaconf import DictConfig, OmegaConf


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
        (self.path / "meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8"
        )
        if metrics is not None:
            (self.path / "metrics.json").write_text(
                json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8"
            )
        return replace(self, finished_on=finished_on, metrics=metrics)


def _flatten_config(cfg: DictConfig) -> list[tuple[str, tp.Any]]:
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


def _matches_key(key: str, pattern: str) -> bool:
    if pattern.startswith("!"):
        return not _matches_key(key, pattern[1:])
    if pattern.endswith(".*"):
        return key.startswith(pattern[:-2] + ".")
    return fnmatchcase(key, pattern)


def config_items(cfg: DictConfig, exclude: tp.Sequence[str]) -> list[tuple[str, tp.Any]]:
    return [
        (key, value)
        for key, value in _flatten_config(cfg)
        if key != "run" and not any(_matches_key(key, pattern) for pattern in exclude)
    ]


def canonical_signature(cfg: DictConfig, exclude: tp.Sequence[str]) -> str:
    items = config_items(cfg, exclude)
    canonical_str = json.dumps(items, sort_keys=True, default=str)
    return sha1(canonical_str.encode()).hexdigest()[:8]


def _str_list(value: tp.Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [str(value)]
    if isinstance(value, tp.Sequence):
        return [str(item) for item in value]
    return []


def _as_cfg(value: DictConfig | tp.Mapping[str, tp.Any] | None) -> DictConfig:
    if OmegaConf.is_config(value):
        return tp.cast(DictConfig, value)
    return OmegaConf.create(value or {})


def _print_run_summary(experiment: Experiment, run: ExperimentRun) -> None:
    print(f"experiment signature: {experiment.signature}")
    print(f"experiment tags: {experiment.tags}")
    print(f"\nrun signature: {run.signature}")
    print(f"run tags: {run.tags}")
    print(f"launched_on: {run.launched_on}")


class ExperimentStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else Path.cwd() / "outputs"
        self.xps_dir = self.root / "xps"

    def _exp_dir(self, signature: str) -> Path:
        return self.xps_dir / signature

    def _run_dir(self, signature: str, run_signature: str) -> Path:
        return self._exp_dir(signature) / run_signature

    def _experiment_config_file(self, signature: str) -> Path:
        return self._exp_dir(signature) / "config.yaml"

    def _runtime_file(self, signature: str, run_signature: str) -> Path:
        return self._run_dir(signature, run_signature) / "runtime.yaml"

    def _meta_file(self, signature: str, run_signature: str) -> Path:
        return self._run_dir(signature, run_signature) / "meta.json"

    def create_experiment(
        self,
        cfg: DictConfig,
        *,
        exclude: tp.Sequence[str] = (),
    ) -> Experiment:
        signature = canonical_signature(cfg, exclude)
        tags = _str_list(OmegaConf.select(cfg, "forge.tags", default=[]))
        exp_dir = self._exp_dir(signature)
        exp_dir.mkdir(parents=True, exist_ok=True)
        return Experiment(signature=signature, tags=tags, config=cfg, path=exp_dir.resolve())

    def register_run(
        self,
        experiment: Experiment,
        *,
        run_signature: str | None = None,
        tags: tp.Sequence[str] | None = None,
        runtime_config: DictConfig | tp.Mapping[str, tp.Any] | None = None,
        activate_run_dir: bool = True,
        verbose: bool = True,
    ) -> ExperimentRun:
        run_signature = run_signature or str(uuid.uuid4())[:8]
        run_dir = self._run_dir(experiment.signature, run_signature)
        run_dir.mkdir(parents=True, exist_ok=True)
        launched_on = datetime.now(timezone.utc).isoformat()
        full_signature = f"{experiment.signature}/{run_signature}"
        run_tags = _str_list(tags) if tags is not None else list(experiment.tags)
        runtime_cfg = _as_cfg(runtime_config)

        OmegaConf.save(experiment.config, self._experiment_config_file(experiment.signature))
        OmegaConf.save(runtime_cfg, self._runtime_file(experiment.signature, run_signature))
        self._meta_file(experiment.signature, run_signature).write_text(
            json.dumps({"launched_on": launched_on, "finished_on": None}, indent=2),
            encoding="utf-8",
        )

        if activate_run_dir:
            os.chdir(run_dir)

        run = ExperimentRun(
            experiment=experiment,
            signature=full_signature,
            tags=run_tags,
            launched_on=launched_on,
            config=runtime_cfg,
            path=run_dir.resolve(),
        )

        if verbose:
            _print_run_summary(experiment, run)

        return run

    def list_runs(self, signature: str | None = None) -> list[ExperimentRun]:
        if not self.xps_dir.exists():
            return []

        pattern = f"{signature}/*/meta.json" if signature else "*/*/meta.json"
        runs = []
        for meta_path in sorted(self.xps_dir.glob(pattern)):
            rel = meta_path.relative_to(self.xps_dir)
            runs.append(self.load_run(rel.parts[0], rel.parts[1]))
        return runs

    def load_run(self, signature: str, run_signature: str) -> ExperimentRun:
        run_dir = self._run_dir(signature, run_signature)
        meta = json.loads(self._meta_file(signature, run_signature).read_text(encoding="utf-8"))
        experiment_config = OmegaConf.load(self._experiment_config_file(signature))
        runtime_config = OmegaConf.load(self._runtime_file(signature, run_signature))
        metrics_file = run_dir / "metrics.json"
        metrics = json.loads(metrics_file.read_text(encoding="utf-8")) if metrics_file.exists() else None

        experiment = Experiment(
            signature=signature,
            tags=_str_list(experiment_config.get("forge", {}).get("tags")),
            config=experiment_config,
            path=self._exp_dir(signature).resolve(),
        )

        return ExperimentRun(
            experiment=experiment,
            signature=f"{signature}/{run_signature}",
            tags=experiment.tags,
            launched_on=meta["launched_on"],
            finished_on=meta.get("finished_on"),
            config=runtime_config,
            path=run_dir.resolve(),
            metrics=metrics,
        )


def start_run(
    cfg: DictConfig,
    *,
    store: ExperimentStore | None = None,
) -> ExperimentRun:
    store_root = OmegaConf.select(cfg, "forge.store", default=None)
    resolved_store = store or ExperimentStore(root=store_root)
    exclude_list = _str_list(OmegaConf.select(cfg, "forge.exclude", default=[]))
    exclude = ("forge.*", *exclude_list)
    verbose = bool(OmegaConf.select(cfg, "forge.verbose", default=True))

    experiment = resolved_store.create_experiment(cfg, exclude=exclude)
    runtime_cfg = _as_cfg(OmegaConf.select(cfg, "runtime", default={}))
    return resolved_store.register_run(experiment, runtime_config=runtime_cfg, verbose=verbose)
