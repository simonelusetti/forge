from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import importlib
import importlib.util
import itertools
import os
from pathlib import Path
import shutil
import sys

from .core import ExperimentStore
from .matching import Selection, compose_cfg


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


@dataclass
class GridRun:
    overrides: list[str]
    outcome: str  # "done" | "failed (exit N)" | "crashed: ExcType: msg"


def grid(
    package: str | None,
    global_overrides: list[str],
    direct: list[list[str]],
    product: dict[str, list],
    *,
    main_module: str = "train",
    config_dir: str | None = None,
    config_name: str = "config",
) -> list[GridRun]:
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
    results: list[GridRun] = []
    for overrides in runs_overrides:
        try:
            exit_code = run(
                package, overrides,
                main_module=main_module,
                config_dir=config_dir,
                config_name=config_name,
            )
            outcome = "done" if exit_code == 0 else f"failed (exit {exit_code})"
        except Exception as exc:
            outcome = f"crashed: {type(exc).__name__}: {exc}"
        finally:
            os.chdir(original_cwd)
        results.append(GridRun(overrides=overrides, outcome=outcome))

    return results


def artifacts(selections: list[Selection], artifact_glob: str) -> list[tuple]:
    results = []
    for selection in selections:
        for run in (selection.runs or []):
            files = sorted(run.path.glob(artifact_glob))
            if files:
                results.append((run, files))
    return results


def failed_runs(*, store: ExperimentStore | None = None) -> list[Selection]:
    """Return one Selection per experiment that has at least one failed run."""
    resolved = store or ExperimentStore()
    by_xp: dict[str, tuple] = {}
    for run in resolved.list_runs():
        if run.status == "failed":
            sig = run.experiment.signature
            if sig not in by_xp:
                by_xp[sig] = (run.experiment, [])
            by_xp[sig][1].append(run)
    return [Selection(xp, runs) for xp, runs in by_xp.values()]


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
