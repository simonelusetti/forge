from __future__ import annotations

from collections import Counter
from fnmatch import fnmatch
from textwrap import indent
from typing import Any

from omegaconf import OmegaConf
from prettytable import PrettyTable

from .commands import Selection
from .core import ExperimentRun, canonical_config, flatten_config




def shorten_keys(keys: list[str]) -> dict[str, str]:
    short = {k: k.rsplit(".", 1)[-1] for k in keys}
    counts = Counter(short.values())
    return {k: v if counts[v] == 1 else k for k, v in short.items()}


def short_config_str(cfg: dict[str, Any], keys: list[str]) -> str:
    """Return a compact ``shortkey=val`` string for the given *keys* in *cfg*.

    Uses :func:`shorten_keys` so each key is represented by its last dotted
    segment where unambiguous.  Returns ``"—"`` when *keys* is empty.
    """
    if not keys:
        return "—"
    short = shorten_keys(keys)
    return "\n".join(f"{short[k]}={cfg.get(k, '—')}" for k in keys)


def xp_config_yaml(cfg) -> str:
    return OmegaConf.to_yaml(OmegaConf.masked_copy(cfg, [k for k in cfg if k != "runtime"]), resolve=True).rstrip()



def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4f}"
    return "—" if v is None else str(v)


def varying_cfg(runs: list[ExperimentRun]) -> tuple[dict[str, dict], list[str]]:
    xp_cfg: dict[str, dict] = {}
    for run in runs:
        if run.experiment.signature not in xp_cfg:
            cfg = run.experiment.config
            exclude = {k for k, _ in flatten_config(cfg) if k.startswith(("forge.", "runtime."))}
            xp_cfg[run.experiment.signature] = dict(canonical_config(cfg, exclude or None))
    all_cfg = {run.signature: xp_cfg[run.experiment.signature] for run in runs}
    all_keys = sorted({k for items in all_cfg.values() for k in items})
    varying = [k for k in all_keys if len({str(items.get(k)) for items in all_cfg.values()}) > 1]
    return all_cfg, varying


def print_metrics_long(runs: list[ExperimentRun], all_cfg: dict, varying: list[str], *, sigs_only: bool = False) -> None:
    for run in runs:
        print(run.signature)
        if not sigs_only:
            print(indent(short_config_str(all_cfg[run.signature], varying), "  "))
        if run.metrics:
            w = max(len(k) for k in run.metrics)
            for k, v in sorted(run.metrics.items()):
                print(f"  {k:{w}}  {_fmt(v)}")
        else:
            print("  (no metrics)")
        print()


def build_metrics_table(
    runs: list[ExperimentRun],
    *,
    long: bool = False,
    sort: list[str] | None = None,
    columns: list[str] | None = None,
) -> PrettyTable:
    if sort:
        runs = sorted(runs, key=lambda r: tuple(
            (r.metrics or {}).get(k, float("inf")) for k in sort
        ))

    all_cfg, varying = varying_cfg(runs)

    metric_keys = sorted({k for run in runs if run.metrics for k in run.metrics})
    if columns:
        metric_keys = [k for k in metric_keys if any(fnmatch(k, pat) for pat in columns)]

    # Transpose when metrics outnumber runs — keeps the table terminal-width-friendly
    if not long and len(metric_keys) > len(runs):
        table = PrettyTable(["metric", *[r.signature for r in runs]])
        table.align = "l"
        table.add_row(["overrides", *[short_config_str(all_cfg[r.signature], varying) for r in runs]])
        for k in metric_keys:
            table.add_row([k, *[_fmt((r.metrics or {}).get(k)) for r in runs]])
        return table

    if long:
        table = PrettyTable(["run", *varying, "launched", "status", *metric_keys])
        table.align = "l"
        for run in runs:
            cfg = all_cfg[run.signature]
            launched = run.launched_on[:16].replace("T", " ")
            metrics = run.metrics or {}
            table.add_row([
                run.signature,
                *[cfg.get(k, "—") for k in varying],
                launched,
                run.status,
                *[_fmt(metrics.get(k)) for k in metric_keys],
            ])
    else:
        table = PrettyTable(["run", "overrides", *metric_keys])
        table.align = "l"
        for run in runs:
            cfg = all_cfg[run.signature]
            metrics = run.metrics or {}
            table.add_row([
                run.signature,
                short_config_str(cfg, varying),
                *[_fmt(metrics.get(k)) for k in metric_keys],
            ])

    return table




def print_matches(matches: list[Selection], xps_only: bool = False) -> int:
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
        print(indent(xp_config_yaml(xp.config), "  "))
        if not xps_only:
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


def print_purge_targets(targets: list[Selection]) -> None:
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
