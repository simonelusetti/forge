from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys
from typing import Any
import uuid

from omegaconf import OmegaConf
import yaml

from . import commands
from .core import ExperimentRun, ExperimentStore
from .matching import query
from .display import (
    build_metrics_table,
    print_matches,
    print_metrics_long,
    print_purge_targets,
    varying_cfg,
)



def _run_command(args: argparse.Namespace) -> int:
    return commands.run(
        args.package,
        args.overrides,
        main_module=args.main_module,
        config_dir=args.config_dir,
        config_name=args.config_name,
    )


def _info_command(args: argparse.Namespace) -> int:
    matches = _query(args)
    if not matches:
        print("no xp found")
        return 0

    if args.sigs_only:
        for match in matches:
            print(match.experiment.signature)
            if not args.xps_only:
                for run in match.runs or []:
                    print(run.signature)
        return 0

    print_matches(matches, xps_only=args.xps_only)
    return 0


def _purge_command(args: argparse.Namespace) -> int:
    targets = _query(args, whole_xps=True)

    if not targets:
        print("no xp found")
        return 0

    print("the following xps/runs will be deleted:")
    print_purge_targets(targets)

    if not args.force:
        answer = input("delete these files? [y/N] ")
        if answer.lower() not in {"y", "yes"}:
            print("aborted")
            return 1

    commands.purge(targets)
    print("deleted")
    return 0


def _store_command(args: argparse.Namespace) -> int:
    targets = _query(args, whole_xps=True)

    if not targets:
        print("no xp found")
        return 0

    destination = commands.store_targets(targets)
    print(f"stored {len(targets)} xp(s) in {destination}")
    return 0


def _metrics_command(args: argparse.Namespace) -> int:
    matches = _query(args)
    if not matches:
        print("no xp found")
        return 0

    runs: list[ExperimentRun] = []
    for match in matches:
        if match.runs is not None:
            runs.extend(match.runs)
        else:
            store = ExperimentStore(root=match.experiment.path.parents[1])
            runs.extend(store.list_runs(match.experiment.signature))

    if not runs:
        print("no runs found")
        return 0

    if args.columns is not None and not args.columns:
        for k in sorted({k for r in runs if r.metrics for k in r.metrics}):
            print(k)
        return 0

    print(build_metrics_table(runs, long=args.long, sort=_sort(args, runs), columns=args.columns or None))
    return 0


def _metrics_long_command(args: argparse.Namespace) -> int:
    matches = _query(args)
    if not matches:
        print("no xp found")
        return 0

    runs: list[ExperimentRun] = []
    for match in matches:
        if match.runs is not None:
            runs.extend(match.runs)
        else:
            store = ExperimentStore(root=match.experiment.path.parents[1])
            runs.extend(store.list_runs(match.experiment.signature))

    if not runs:
        print("no runs found")
        return 0

    all_cfg, varying = varying_cfg(runs)
    print_metrics_long(runs, all_cfg, varying, sigs_only=args.sigs_only)

    store_root = runs[0].experiment.path.parents[1]
    metrics_dir = store_root / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)

    all_metric_keys = sorted({k for r in runs if r.metrics for k in r.metrics})
    csv_path = metrics_dir / (str(uuid.uuid4())[:8] + ".csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["run", "overrides", *all_metric_keys])
        for run in runs:
            overrides = "  ".join(f"{k}={all_cfg[run.signature].get(k, '—')}" for k in varying)
            writer.writerow([run.signature, overrides, *[(run.metrics or {}).get(k, "") for k in all_metric_keys]])

    print(f"→ {csv_path}")
    return 0


def _artifact_command(args: argparse.Namespace) -> int:
    # Last positional is always the artifact glob; everything before it is the
    # run pattern — same convention as other commands, no special separator needed.
    *run_patterns, artifact_glob = args.args

    selections = query(
        run_patterns,
        package=args.package,
        config_dir=args.config_dir,
        config_name=args.config_name,
        store=ExperimentStore(),
        strict=args.strict,

    )
    results = commands.artifacts(selections, artifact_glob)

    if not results:
        print("no artifacts found")
        return 0

    for run, files in results:
        print(f"\nrun: {run.signature}")
        for f in files:
            print(f"  {f.relative_to(run.path)}")
    return 0


def _clean_command(args: argparse.Namespace) -> int:
    store = ExperimentStore()
    targets = commands.failed_runs(store=store)

    if not targets:
        print("no failed runs found")
        return 0

    print("the following failed runs will be deleted:")
    print_purge_targets(targets)

    if not args.force:
        answer = input("delete these runs? [y/N] ")
        if answer.lower() not in {"y", "yes"}:
            print("aborted")
            return 1

    n = sum(len(t.runs) for t in targets if t.runs)
    commands.purge(targets)
    print(f"deleted {n} failed run(s)")
    return 0


def _grid_command(args: argparse.Namespace) -> int:
    global_overrides: list[str] = list(args.globals or [])
    direct: list[list[str]] = [list(r) for r in (args.direct or [])]
    product: dict[str, list[Any]] = {}

    # Auto-detect a YAML file as the first positional arg: it must not contain
    # "=" (ruling out Hydra overrides) and must exist on disk.
    grid_file = args.grid_file
    if not grid_file and global_overrides and "=" not in global_overrides[0]:
        candidate = Path(global_overrides[0])
        if candidate.is_file():
            grid_file = str(candidate)
            global_overrides = global_overrides[1:]

    if grid_file:
        with open(grid_file, encoding="utf-8") as f:
            file_spec = yaml.safe_load(f)
        if not isinstance(file_spec, dict):
            return _usage_error(f"grid file {grid_file!r} must be a YAML mapping")
        # File values are base; CLI values layer on top
        global_overrides = list(file_spec.get("globals", [])) + global_overrides
        direct = [list(r) for r in file_spec.get("direct", [])] + direct
        for k, v in file_spec.get("product", {}).items():
            product.setdefault(k, v)

    for sweep in (args.sweeps or []):
        key, sep, values_str = sweep.partition("=")
        if not sep or not values_str:
            return _usage_error(f"--sweep expects KEY=V1,V2,...  got {sweep!r}")
        product[key] = values_str.split(",")

    if not direct and not product and not global_overrides:
        return _usage_error(
            "nothing to run — provide globals, --run, --sweep, or --file"
        )

    results = commands.grid(
        args.package,
        global_overrides,
        direct,
        product,
        main_module=args.main_module,
        config_dir=args.config_dir,
        config_name=args.config_name,
    )

    if results:
        print()
        print(build_metrics_table(results, long=args.long, sort=_sort(args, results), columns=args.columns or None))
    return 0



def _sort(args: argparse.Namespace, runs: list[ExperimentRun]) -> list[str]:
    if args.sort:
        return list(args.sort)
    if runs:
        return list(OmegaConf.select(runs[0].experiment.config, "forge.sort", default=[]) or [])
    return []


def _query(args: argparse.Namespace, *, whole_xps: bool = False) -> list[commands.Selection]:
    return query(
        args.patterns,
        package=args.package,
        config_dir=args.config_dir,
        config_name=args.config_name,
        store=ExperimentStore(),
        strict=args.strict,

        whole_xps=whole_xps,
    )


def _usage_error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge")
    parser.add_argument("-P", "--package", default=None)
    parser.add_argument("-M", "--main-module", default="train")
    parser.add_argument("--config-dir")
    parser.add_argument("--config-name", default="config")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("overrides", nargs="*")
    run_parser.set_defaults(handler=_run_command)

    info_parser = subparsers.add_parser("info")
    info_parser.add_argument("-S", "--sigs-only", action="store_true")
    info_parser.add_argument("-X", "--xps-only", action="store_true")
    info_parser.add_argument("--strict", action="store_true")

    info_parser.add_argument("patterns", nargs="*")
    info_parser.set_defaults(handler=_info_command)

    purge_parser = subparsers.add_parser("purge")
    purge_parser.add_argument("--strict", action="store_true")
    purge_parser.add_argument("-F", "--force", action="store_true")
    purge_parser.add_argument("patterns", nargs="*")
    purge_parser.set_defaults(handler=_purge_command)

    store_parser = subparsers.add_parser("store")
    store_parser.add_argument("--strict", action="store_true")
    store_parser.add_argument("patterns", nargs="*")
    store_parser.set_defaults(handler=_store_command)

    metrics_parser = subparsers.add_parser("metrics")
    metrics_parser.add_argument("--strict", action="store_true")
    metrics_parser.add_argument("-l", "--long", action="store_true")
    metrics_parser.add_argument("-s", "--sort", nargs="+", metavar="METRIC")
    metrics_parser.add_argument("-c", "--columns", nargs="*", metavar="GLOB")
    metrics_parser.add_argument("patterns", nargs="*")
    metrics_parser.set_defaults(handler=_metrics_command)

    metrics_long_parser = subparsers.add_parser("metrics-long")
    metrics_long_parser.add_argument("--strict", action="store_true")
    metrics_long_parser.add_argument("-S", "--sigs-only", action="store_true")
    metrics_long_parser.add_argument("patterns", nargs="*")
    metrics_long_parser.set_defaults(handler=_metrics_long_command)

    artifact_parser = subparsers.add_parser("artifact")
    artifact_parser.add_argument("args", nargs="+", metavar="PATTERN")
    artifact_parser.add_argument("--strict", action="store_true")
    artifact_parser.set_defaults(handler=_artifact_command)

    clean_parser = subparsers.add_parser("clean")
    clean_parser.add_argument("-F", "--force", action="store_true")
    clean_parser.set_defaults(handler=_clean_command)

    grid_parser = subparsers.add_parser("grid")
    grid_parser.add_argument("globals", nargs="*")
    grid_parser.add_argument("-l", "--long", action="store_true")
    grid_parser.add_argument("-s", "--sort", nargs="+", metavar="METRIC")
    grid_parser.add_argument("-c", "--columns", nargs="+", metavar="GLOB")
    grid_parser.add_argument("--run", action="append", nargs="+", dest="direct", metavar="OVERRIDE")
    grid_parser.add_argument("--sweep", action="append", dest="sweeps", metavar="KEY=V1,V2,...")
    grid_parser.add_argument("--file", dest="grid_file", metavar="YAML")
    grid_parser.set_defaults(handler=_grid_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    import argcomplete
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "metrics" and argv[1] == "long":
        argv = ["metrics-long", *argv[2:]]
    parser = _build_parser()
    argcomplete.autocomplete(parser)
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
