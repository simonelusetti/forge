from __future__ import annotations

from datetime import datetime
import importlib
import importlib.util
from pathlib import Path
import shutil
import sys

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


def _resolve_config_dir(package: str | None, config_dir: str | None) -> Path:
    """Return the Hydra config directory to use.

    Priority:
    1. Explicit ``--config-dir`` argument.
    2. ``<package>/conf/`` when a package name is given and importable.
    3. ``<cwd>/conf/`` as the default for directory-based projects.
    """
    if config_dir:
        return Path(config_dir)
    if package:
        spec = importlib.util.find_spec(package)
        if spec is None or not spec.submodule_search_locations:
            raise ModuleNotFoundError(
                f"Package {package!r} not found. "
                f"Run from the project directory or pass --config-dir."
            )
        return Path(next(iter(spec.submodule_search_locations))) / "conf"
    # Default: conf/ relative to cwd
    cwd_conf = Path.cwd() / "conf"
    if not cwd_conf.is_dir():
        raise FileNotFoundError(
            f"No config directory found. Expected {cwd_conf} to exist, "
            f"or pass --config-dir / -P <package>."
        )
    return cwd_conf


def _load_module(package: str | None, main_module: str):
    """Import and return the main module.

    When *package* is given, imports ``<package>.<main_module>`` normally.
    Otherwise loads ``<main_module>.py`` from the current working directory.
    """
    if package:
        return importlib.import_module(f"{package}.{main_module}")

    module_path = Path.cwd() / f"{main_module}.py"
    if not module_path.exists():
        raise FileNotFoundError(
            f"Module file {module_path} not found. "
            f"Run from the project directory or pass -M / -P."
        )
    spec = importlib.util.spec_from_file_location(main_module, module_path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    # Make the project directory importable so relative imports inside the
    # module file work (e.g. ``from . import utils``).
    cwd_str = str(Path.cwd())
    if cwd_str not in sys.path:
        sys.path.insert(0, cwd_str)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def compose_cfg(
    package: str | None,
    overrides: list[str],
    *,
    config_dir: str | None = None,
    config_name: str = "config",
) -> DictConfig:
    resolved_config_dir = _resolve_config_dir(package, config_dir)
    with initialize_config_dir(config_dir=str(resolved_config_dir.resolve()), version_base=None):
        return compose(config_name=config_name, overrides=overrides)


def run(
    package: str | None,
    overrides: list[str],
    *,
    main_module: str = "train",
    config_dir: str | None = None,
    config_name: str = "config",
) -> int:
    cfg = compose_cfg(package, overrides, config_dir=config_dir, config_name=config_name)
    module = _load_module(package, main_module)
    return int(module.main(cfg) or 0)


def info(
    package: str | None,
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
    package: str | None,
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
