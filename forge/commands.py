from __future__ import annotations

from datetime import datetime, timezone
import json
import importlib
import importlib.util
import itertools
import os
from pathlib import Path
import shutil
import sys

from omegaconf import OmegaConf

from .core import ExperimentRun, ExperimentStore
from .matching import Selection, compose_cfg


class GridRunError(RuntimeError):
    def __init__(self, runs: list[ExperimentRun],
                 failures: list[tuple[list[str], Exception]]):
        self.runs = runs
        self.failures = failures
        super().__init__(f"{len(failures)} grid run(s) failed")


def _load_module(package: str | None, main_module: str):
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
    cwd_str = str(Path.cwd())
    if cwd_str not in sys.path:
        sys.path.insert(0, cwd_str)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


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


def grid(
    package: str | None,
    global_overrides: list[str],
    direct: list[list[str]],
    product: dict[str, list],
    *,
    main_module: str = "train",
    config_dir: str | None = None,
    config_name: str = "config",
) -> list[ExperimentRun]:
    runs_overrides = [global_overrides + r for r in direct]

    if product:
        keys = list(product.keys())
        value_lists = [[str(v) for v in product[k]] for k in keys]
        for combo in itertools.product(*value_lists):
            combo_overrides = [f"{k}={v}" for k, v in zip(keys, combo)]
            runs_overrides.append(global_overrides + combo_overrides)

    if not runs_overrides:
        runs_overrides.append(list(global_overrides))

    original_cwd = Path.cwd()
    results: list[ExperimentRun] = []
    failures: list[tuple[list[str], Exception]] = []

    for overrides in runs_overrides:
        iteration_store = None
        previous_runs: set[str] = set()
        failure = None
        try:
            cfg = compose_cfg(package, overrides, config_dir=config_dir,
                              config_name=config_name)
            iteration_store = ExperimentStore(root=OmegaConf.select(cfg, "forge.store", default=None))
            previous_runs = {r.signature for r in iteration_store.list_runs()}
            exit_code = run(package, overrides, main_module=main_module,
                            config_dir=config_dir, config_name=config_name)
            if exit_code:
                raise RuntimeError(f"entry point returned exit code {exit_code}")
        except Exception as error:
            failure = error
            failures.append((list(overrides), error))
        finally:
            os.chdir(original_cwd)
        if iteration_store is not None:
            created = [r for r in iteration_store.list_runs()
                       if r.signature not in previous_runs]
            if failure is not None:
                for recorded in created:
                    if recorded.status == "running":
                        meta_path = recorded.path / "meta.json"
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        meta["status"] = "failed"
                        meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True),
                                             encoding="utf-8")
                created = [iteration_store.load_run(*r.signature.split("/", 1))
                           for r in created]
            results.extend(created)

    if failures:
        raise GridRunError(results, failures)
    return results


def artifacts(selections: list[Selection], artifact_glob: str) -> list[tuple]:
    results = []
    for selection in selections:
        runs = selection.runs if selection.runs is not None else ExperimentStore(root=selection.experiment.path.parents[1]).list_runs(selection.experiment.signature)
        for run in runs:
            files = sorted(run.path.glob(artifact_glob))
            if files:
                results.append((run, files))
    return results


def failed_runs(*, store: ExperimentStore | None = None) -> list[Selection]:
    resolved = store or ExperimentStore()
    return [
        Selection(sel.experiment, [r for r in (sel.runs or []) if r.status == "failed"])
        for sel in resolved.all_selections()
        if any(r.status == "failed" for r in (sel.runs or []))
    ]


def purge(targets: list[Selection]) -> None:
    for target in targets:
        if target.runs is None:
            shutil.rmtree(target.experiment.path)
        else:
            for run in target.runs:
                shutil.rmtree(run.path)
            xp_path = target.experiment.path
            if xp_path.exists() and not any(p.is_dir() for p in xp_path.iterdir()):
                shutil.rmtree(xp_path)


def store_targets(targets: list[Selection], *, root: Path | str | None = None) -> Path:
    root = Path(root) if root else targets[0].experiment.path.parents[1]
    destination = root / "stored" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    for target in targets:
        if target.runs is None:
            shutil.copytree(target.experiment.path, destination / "xps" / target.experiment.signature)
            shutil.rmtree(target.experiment.path)
        else:
            xp_dir = destination / "xps" / target.experiment.signature
            xp_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target.experiment.path / "config.yaml", xp_dir / "config.yaml")
            for run in target.runs:
                shutil.copytree(run.path, xp_dir / run.signature.split("/", 1)[1])
                shutil.rmtree(run.path)
    return destination
