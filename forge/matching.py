from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path

from hydra import compose, initialize_config_dir
from omegaconf import DictConfig, OmegaConf

from .core import Experiment, ExperimentRun, ExperimentStore, canonical_config, forge_exclude


@dataclass(frozen=True)
class Selection:
    experiment: Experiment
    runs: list[ExperimentRun] | None



def _resolve_config_dir(package: str | None, config_dir: str | None) -> Path:
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
    cwd_conf = Path.cwd() / "conf"
    if not cwd_conf.is_dir():
        raise FileNotFoundError(
            f"No config directory found. Expected {cwd_conf} to exist, "
            f"or pass --config-dir / -P <package>."
        )
    return cwd_conf


def compose_cfg(
    package: str | None,
    overrides: list[str],
    *,
    config_dir: str | None = None,
    config_name: str = "config",
) -> DictConfig:
    resolved = _resolve_config_dir(package, config_dir)
    with initialize_config_dir(config_dir=str(resolved.resolve()), version_base=None):
        return compose(config_name=config_name, overrides=overrides)



def query(
    patterns: list[str],
    *,
    mode: str = "overrides",
    package: str | None = None,
    config_dir: str | None = None,
    config_name: str = "config",
    store: ExperimentStore | None = None,
    strict: bool = False,
    all_runs: bool = False,
    whole_xps: bool = False,
) -> list[Selection]:
    """Return matching selections for *patterns* using the given *mode*.

    Modes:
    - ``"overrides"``: Hydra override strings, matched against stored configs
    - ``"sigs"``:      partial experiment/run signature strings
    - ``"tags"``:      tag name strings
    """
    resolved_store = store or ExperimentStore()
    if mode == "sigs":
        matches = select_signatures(patterns, store=resolved_store, all_runs=all_runs)
    elif mode == "tags":
        matches = tag_matches(patterns, store=resolved_store, strict=strict)
    else:
        base_cfg = compose_cfg(package, [], config_dir=config_dir, config_name=config_name)
        target_cfg = compose_cfg(package, patterns, config_dir=config_dir, config_name=config_name)
        matches = config_matches(base_cfg, target_cfg, store=resolved_store, strict=strict)
    return whole_experiments(matches) if whole_xps else matches



def config_matches(
    base_cfg: DictConfig,
    target_cfg: DictConfig,
    *,
    store: ExperimentStore | None = None,
    strict: bool = False,
) -> list[Selection]:
    exclude = forge_exclude(target_cfg)
    if strict:
        constraints = canonical_config(target_cfg, exclude)
    else:
        base_items = dict(canonical_config(base_cfg, exclude))
        constraints = [(k, v) for k, v in canonical_config(target_cfg, exclude) if base_items.get(k) != v]
    return [
        match for match in _all_matches(store)
        if all(OmegaConf.select(match.experiment.config, k) == v for k, v in constraints)
    ]


def tag_matches(
    tags: list[str],
    *,
    store: ExperimentStore | None = None,
    strict: bool = False,
) -> list[Selection]:
    if not tags:
        return [] if strict else _all_matches(store)
    check = all if strict else any
    selected = []
    for match in _all_matches(store):
        xp_matches = check(tag in match.experiment.tags for tag in tags)
        runs = [run for run in match.runs or [] if xp_matches or check(tag in run.tags for tag in tags)]
        if xp_matches or runs:
            selected.append(Selection(match.experiment, runs))
    return selected


def whole_experiments(matches: list[Selection]) -> list[Selection]:
    return [Selection(match.experiment, None) for match in matches]


def select_signatures(
    signatures: list[str],
    *,
    store: ExperimentStore | None = None,
    all_runs: bool = False,
) -> list[Selection]:
    all_matches = _all_matches(store)
    selected: dict[str, tuple[Experiment, list[ExperimentRun] | None]] = {}
    for signature in signatures:
        for match in all_matches:
            xp = match.experiment
            if "/" not in signature and xp.signature.startswith(signature):
                selected[xp.signature] = (xp, None)
            elif "/" in signature:
                xp_sig, run_sig = signature.split("/", 1)
                if xp.signature.startswith(xp_sig):
                    if all_runs:
                        selected[xp.signature] = (xp, None)
                    else:
                        runs = [run for run in match.runs or [] if run.signature.split("/", 1)[1].startswith(run_sig)]
                        if runs:
                            existing = selected.get(xp.signature, (xp, []))[1]
                            seen = {r.signature for r in existing}
                            selected[xp.signature] = (xp, [*existing, *(r for r in runs if r.signature not in seen)])
    return [Selection(xp, runs) for xp, runs in selected.values()]


def _all_matches(store: ExperimentStore | None = None) -> list[Selection]:
    experiments: dict[str, Experiment] = {}
    runs: dict[str, list[ExperimentRun]] = {}
    for run in (store or ExperimentStore()).list_runs():
        signature = run.experiment.signature
        experiments[signature] = run.experiment
        runs.setdefault(signature, []).append(run)
    return [Selection(experiment, runs[signature]) for signature, experiment in experiments.items()]


