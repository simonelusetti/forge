from __future__ import annotations

from dataclasses import dataclass
import typing as tp

from omegaconf import DictConfig, OmegaConf

from .core import Experiment, ExperimentRun, ExperimentStore, _str_list, config_items


@dataclass(frozen=True)
class Selection:
    experiment: Experiment
    runs: list[ExperimentRun] | None


def config_matches(
    base_cfg: DictConfig,
    target_cfg: DictConfig,
    *,
    store: ExperimentStore | None = None,
    strict: bool = False,
) -> list[Selection]:
    exclude = ("forge.*", *_str_list(OmegaConf.select(target_cfg, "forge.exclude", default=[])))
    constraints = config_items(target_cfg, exclude) if strict else _changed_items(base_cfg, target_cfg, exclude)
    return [match for match in _all_matches(store) if _matches_constraints(match.experiment.config, constraints)]


def tag_matches(
    tags: list[str],
    *,
    store: ExperimentStore | None = None,
    strict: bool = False,
) -> list[Selection]:
    if not tags:
        return [] if strict else _all_matches(store)

    selected = []
    for match in _all_matches(store):
        xp_matches = _tag_match(match.experiment.tags, tags, strict)
        runs = [run for run in match.runs or [] if xp_matches or _tag_match(run.tags, tags, strict)]
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
                        runs = [run for run in match.runs or [] if _run_sig(run).startswith(run_sig)]
                        if runs:
                            selected[xp.signature] = (xp, _collect_runs(selected.get(xp.signature, (xp, []))[1], runs))
    return [Selection(xp, runs) for xp, runs in selected.values()]


def _all_matches(store: ExperimentStore | None = None) -> list[Selection]:
    experiments: dict[str, Experiment] = {}
    runs: dict[str, list[ExperimentRun]] = {}
    for run in (store or ExperimentStore()).list_runs():
        signature = run.experiment.signature
        experiments[signature] = run.experiment
        runs.setdefault(signature, []).append(run)
    return [Selection(experiment, runs[signature]) for signature, experiment in experiments.items()]


def _changed_items(base_cfg: DictConfig, target_cfg: DictConfig, exclude: tp.Sequence[str]) -> list[tuple[str, tp.Any]]:
    base_items = dict(config_items(base_cfg, exclude))
    return [(k, v) for k, v in config_items(target_cfg, exclude) if base_items.get(k) != v]


def _matches_constraints(cfg: DictConfig, constraints: list[tuple[str, tp.Any]]) -> bool:
    return all(OmegaConf.select(cfg, key) == value for key, value in constraints)


def _tag_match(values: list[str], tags: list[str], strict: bool) -> bool:
    return all(tag in values for tag in tags) if strict else any(tag in values for tag in tags)


def _run_sig(run: ExperimentRun) -> str:
    return run.signature.split("/", 1)[1]


def _collect_runs(current: list[ExperimentRun], new: list[ExperimentRun]) -> list[ExperimentRun]:
    existing = {run.signature for run in current}
    return [*current, *(run for run in new if run.signature not in existing)]
