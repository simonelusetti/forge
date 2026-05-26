from __future__ import annotations

import argparse
import sys
from textwrap import indent

from omegaconf import OmegaConf
from prettytable import PrettyTable

from . import commands
from .core import ExperimentRun, ExperimentStore, config_items


def _run_command(args: argparse.Namespace) -> int:
    return commands.run(
        args.package,
        args.overrides,
        main_module=args.main_module,
        config_dir=args.config_dir,
        config_name=args.config_name,
    )


def _info_command(args: argparse.Namespace) -> int:
    mode_error = _mode_error(args)
    if mode_error:
        return _usage_error(mode_error)

    sigs_or_tags = args.sigs or args.tags
    if args.sigs:
        if args.sigs_only or args.xps_only or args.strict:
            return _usage_error("signature info does not support --sigs-only, --xps-only, or --strict")

    if args.tags:
        if args.sigs_only or args.xps_only or args.all_runs:
            return _usage_error("tag info does not support --sigs-only, --xps-only, or --all-runs")

    matches = _select(args)

    if not matches:
        print("no xp found")
        return 0

    if sigs_or_tags:
        return _print_config_matches(matches)

    if args.sigs_only:
        for match in matches:
            print(match.experiment.signature)
            if not args.xps_only:
                for run in match.runs or []:
                    print(run.signature)
        return 0

    _print_summary_matches(matches, xps_only=args.xps_only)
    return 0


def _purge_command(args: argparse.Namespace) -> int:
    mode_error = _mode_error(args)
    if mode_error:
        return _usage_error(mode_error)

    if args.sigs_only or args.xps_only:
        return _usage_error("purge does not support --sigs-only or --xps-only")

    targets = _select(args, whole_xps=True)

    if not targets:
        print("no xp found")
        return 0

    print("the following xps/runs will be deleted:")
    _print_purge_targets(targets)

    if not args.force:
        answer = input("delete these files? [y/N] ")
        if answer.lower() not in {"y", "yes"}:
            print("aborted")
            return 1

    commands.purge(targets)
    print("deleted")
    return 0


def _store_command(args: argparse.Namespace) -> int:
    mode_error = _mode_error(args)
    if mode_error:
        return _usage_error(mode_error)

    targets = _select(args, whole_xps=True)

    if not targets:
        print("no xp found")
        return 0

    destination = commands.store_targets(targets)
    print(f"stored {len(targets)} xp(s) in {destination}")
    return 0


def _metrics_command(args: argparse.Namespace) -> int:
    if args.sigs and args.tags:
        return _usage_error("command accepts one mode at a time")
    if not args.sigs and not args.tags and any("=" not in p for p in args.patterns):
        return _usage_error("override mode expects Hydra overrides like key=value")

    matches = _select(args)
    if not matches:
        print("no xp found")
        return 0

    # Resolve runs=None (whole-experiment selections from sig mode) by loading from store
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

    print(_build_metrics_table(runs))
    return 0


def _build_metrics_table(runs: list[ExperimentRun]) -> PrettyTable:
    # Config keys that differ across experiments — identical keys add no information
    all_cfg = {
        run.signature: dict(config_items(run.experiment.config, ("forge.*", "runtime.*")))
        for run in runs
    }
    all_keys = sorted({k for items in all_cfg.values() for k in items})
    varying = [k for k in all_keys if len({str(items.get(k)) for items in all_cfg.values()}) > 1]

    # Metric keys present in any run
    metric_keys = sorted({k for run in runs if run.metrics for k in run.metrics})

    table = PrettyTable(["run", *varying, "launched", "status", *metric_keys])
    table.align = "l"

    for run in runs:
        cfg = all_cfg[run.signature]
        launched = run.launched_on[:16].replace("T", " ")
        status = "done" if run.finished_on else "running"
        metrics = run.metrics or {}
        table.add_row([
            run.signature,
            *[cfg.get(k, "—") for k in varying],
            launched,
            status,
            *[metrics.get(k, "—") for k in metric_keys],
        ])

    return table


def _select(args: argparse.Namespace, *, whole_xps: bool = False) -> list[commands.Selection]:
    return commands.select(
        args.package,
        args.patterns,
        mode=_mode(args),
        config_dir=args.config_dir,
        config_name=args.config_name,
        store=ExperimentStore(),
        strict=getattr(args, "strict", False),
        all_runs=getattr(args, "all_runs", False),
        whole_xps=whole_xps,
    )


def _mode(args: argparse.Namespace) -> str:
    return "sigs" if args.sigs else "tags" if args.tags else "overrides"


def _print_config_matches(matches: list[commands.Selection]) -> int:
    if not matches:
        print("no xp found")
        return 0

    print(f"found {len(matches)} xp(s)")
    for match in matches:
        print()
        xp = match.experiment
        print(f"xp: {xp.signature}")
        print(f"path: {xp.path}")
        print("config:")
        print(indent(_xp_config_yaml(xp.config), "  "))
        for run in match.runs or []:
            print()
            print(f"  run: {run.signature}")
            print(f"  path: {run.path}")
            print("  runtime:")
            print(indent(OmegaConf.to_yaml(run.config, resolve=True).rstrip(), "    "))
            if run.metrics is not None:
                print("  metrics:")
                for k, v in run.metrics.items():
                    print(f"    {k}: {v}")
    return 0


def _print_summary_matches(matches: list[commands.Selection], *, xps_only: bool) -> None:
    print(f"found {len(matches)} xp(s)")
    for match in matches:
        xp = match.experiment
        print()
        print(f"xp: {xp.signature}")
        print(f"path: {xp.path}")
        if not xps_only:
            runs = match.runs or []
            print(f"runs: {len(runs)}")
            for run in runs:
                status = "done" if run.finished_on else "running"
                metrics_str = f"  {run.metrics}" if run.metrics else ""
                print(f"  - {run.signature}  [{status}]{metrics_str}")
                print(f"    path: {run.path}")


def _print_purge_targets(targets: list[commands.Selection]) -> None:
    for target in targets:
        xp = target.experiment
        print()
        runs = target.runs or []
        marker = "xp" if target.runs is None else "xp runs"
        print(f"{marker}: {xp.signature}")
        print(f"path: {xp.path}")
        print(f"runs: {len(runs)}")
        for run in runs:
            print(f"  - {run.signature}")
            print(f"    path: {run.path}")


def _mode_error(args: argparse.Namespace) -> str | None:
    if args.sigs and args.tags:
        return "command accepts one mode at a time"
    if not args.sigs and any("=" not in arg for arg in args.patterns):
        if not args.tags:
            return "override mode expects Hydra overrides like key=value"
    if not args.sigs and args.all_runs:
        return "--all-runs is only supported with signature mode"
    return None


def _usage_error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _xp_config_yaml(cfg) -> str:
    data = OmegaConf.to_container(cfg, resolve=True)
    if isinstance(data, dict):
        data.pop("runtime", None)
    return OmegaConf.to_yaml(OmegaConf.create(data), resolve=True).rstrip()


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
    info_parser.add_argument("--sigs-only", action="store_true")
    info_parser.add_argument("--xps-only", action="store_true")
    info_parser.add_argument("--strict", action="store_true")
    info_parser.add_argument("-A", "--all-runs", action="store_true")
    info_parser.add_argument("-S", "--sigs", action="store_true")
    info_parser.add_argument("-T", "--tags", action="store_true")
    info_parser.add_argument("patterns", nargs="*")
    info_parser.set_defaults(handler=_info_command)

    purge_parser = subparsers.add_parser("purge")
    purge_parser.add_argument("--sigs-only", action="store_true", help=argparse.SUPPRESS)
    purge_parser.add_argument("--xps-only", action="store_true", help=argparse.SUPPRESS)
    purge_parser.add_argument("--strict", action="store_true")
    purge_parser.add_argument("-A", "--all-runs", action="store_true")
    purge_parser.add_argument("-S", "--sigs", action="store_true")
    purge_parser.add_argument("-T", "--tags", action="store_true")
    purge_parser.add_argument("-f", "--force", action="store_true")
    purge_parser.add_argument("patterns", nargs="*")
    purge_parser.set_defaults(handler=_purge_command)

    store_parser = subparsers.add_parser("store")
    store_parser.add_argument("--strict", action="store_true")
    store_parser.add_argument("-A", "--all-runs", action="store_true")
    store_parser.add_argument("-S", "--sigs", action="store_true")
    store_parser.add_argument("-T", "--tags", action="store_true")
    store_parser.add_argument("patterns", nargs="*")
    store_parser.set_defaults(handler=_store_command)

    metrics_parser = subparsers.add_parser("metrics")
    metrics_parser.add_argument("--strict", action="store_true")
    metrics_parser.add_argument("-S", "--sigs", action="store_true")
    metrics_parser.add_argument("-T", "--tags", action="store_true")
    metrics_parser.add_argument("patterns", nargs="*")
    metrics_parser.set_defaults(handler=_metrics_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
