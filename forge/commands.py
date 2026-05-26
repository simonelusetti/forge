from __future__ import annotations

from datetime import datetime
import importlib
import importlib.util
from pathlib import Path
import shutil

from hydra import compose, initialize_config_dir
from omegaconf import DictConfig

from .core import ExperimentStore
from .matching import (
    Selection,
    config_matches,
    select_signatures,
    tag_matches,
    whole_experiments,
)


def _package_config_dir(package: str) -> Path:
    spec = importlib.util.find_spec(package)
    package_dir = Path(next(iter(spec.submodule_search_locations)))
    return package_dir / "conf"


def compose_cfg(
    package: str,
    overrides: list[str],
    *,
    config_dir: str | None = None,
    config_name: str = "config",
) -> DictConfig:
    resolved_config_dir = Path(config_dir) if config_dir else _package_config_dir(package)
    with initialize_config_dir(config_dir=str(resolved_config_dir.resolve()), version_base=None):
        return compose(config_name=config_name, overrides=overrides)


def run(
    package: str,
    overrides: list[str],
    *,
    main_module: str = "train",
    config_dir: str | None = None,
    config_name: str = "config",
) -> int:
    cfg = compose_cfg(
        package,
        overrides,
        config_dir=config_dir,
        config_name=config_name,
    )
    module = importlib.import_module(f"{package}.{main_module}")
    return int(module.main(cfg) or 0)


def info(
    package: str,
    overrides: list[str],
    *,
    config_dir: str | None = None,
    config_name: str = "config",
    store: ExperimentStore | None = None,
    strict: bool = False,
) -> list[Selection]:
    base_cfg = compose_cfg(package, [], config_dir=config_dir, config_name=config_name)
    target_cfg = compose_cfg(package, overrides, config_dir=config_dir, config_name=config_name)
    return config_matches(base_cfg, target_cfg, store=store, strict=strict)


def select(
    package: str,
    args: list[str],
    *,
    mode: str = "overrides",
    config_dir: str | None = None,
    config_name: str = "config",
    store: ExperimentStore | None = None,
    strict: bool = False,
    all_runs: bool = False,
    whole_xps: bool = False,
) -> list[Selection]:
    store = store or ExperimentStore()
    if mode == "sigs":
        return select_signatures(args, store=store, all_runs=all_runs)
    matches = tag_matches(args, store=store, strict=strict) if mode == "tags" else info(
        package,
        args,
        config_dir=config_dir,
        config_name=config_name,
        store=store,
        strict=strict,
    )
    return whole_experiments(matches) if whole_xps else matches


def purge(targets: list[Selection]) -> None:
    for target in targets:
        if target.runs is None:
            shutil.rmtree(target.experiment.path)
        else:
            for run in target.runs:
                shutil.rmtree(run.path)


def store_targets(targets: list[Selection], *, root: Path | str | None = None) -> Path:
    root = Path(root) if root else targets[0].experiment.path.parents[1]
    destination = root / "stored" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for target in targets:
        if target.runs is None:
            shutil.copytree(target.experiment.path, destination / "xps" / target.experiment.signature)
        else:
            xp_dir = destination / "xps" / target.experiment.signature
            xp_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target.experiment.path / "config.yaml", xp_dir / "config.yaml")
            for run in target.runs:
                shutil.copytree(run.path, xp_dir / run.signature.split("/", 1)[1])
    return destination
