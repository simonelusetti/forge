from __future__ import annotations

from textwrap import indent
from typing import Any

from omegaconf import OmegaConf
from prettytable import PrettyTable

from .commands import GridRun, Selection
from .core import ExperimentRun, config_items


# ---------------------------------------------------------------------------
# Config / override formatting
# ---------------------------------------------------------------------------

def parse_overrides(overrides: list[str]) -> dict[str, str]:
    """Parse a list of Hydra override strings into a plain key→value dict."""
    result: dict[str, str] = {}
    for override in overrides:
        stripped = override.lstrip("+~")  # strip +, ++, ~, ~~
        if "=" in stripped:
            key, _, val = stripped.partition("=")
            result[key] = val
    return result


def shorten_keys(keys: list[str]) -> dict[str, str]:
    """Map each full dotted key to its last segment (e.g. ``model.optim.lr`` → ``lr``).

    When two keys share the same last segment the full key is kept for both so
    the meaning stays unambiguous.
    """
    short = {k: k.rsplit(".", 1)[-1] for k in keys}
    counts: dict[str, int] = {}
    for v in short.values():
        counts[v] = counts.get(v, 0) + 1
    return {k: v if counts[v] == 1 else k for k, v in short.items()}


def short_config_str(cfg: dict[str, Any], keys: list[str]) -> str:
    """Return a compact ``shortkey=val`` string for the given *keys* in *cfg*.

    Uses :func:`shorten_keys` so each key is represented by its last dotted
    segment where unambiguous.  Returns ``"—"`` when *keys* is empty.
    """
    if not keys:
        return "—"
    short = shorten_keys(keys)
    return "  ".join(f"{short[k]}={cfg.get(k, '—')}" for k in keys)


def xp_config_yaml(cfg) -> str:
    data = OmegaConf.to_container(cfg, resolve=True)
    if isinstance(data, dict):
        data.pop("runtime", None)
    return OmegaConf.to_yaml(OmegaConf.create(data), resolve=True).rstrip()


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------

def build_metrics_table(runs: list[ExperimentRun], *, long: bool = False) -> PrettyTable:
    """Build a PrettyTable summarising *runs* and their metrics.

    In short mode (default) the varying config keys are collapsed into a single
    ``overrides`` column using :func:`short_config_str`.  Pass ``long=True``
    for the full breakdown with one column per key plus launched/status.
    """
    all_cfg = {
        run.signature: dict(config_items(run.experiment.config, ("forge.*", "runtime.*")))
        for run in runs
    }
    all_keys = sorted({k for items in all_cfg.values() for k in items})
    varying = [k for k in all_keys if len({str(items.get(k)) for items in all_cfg.values()}) > 1]

    metric_keys = sorted({k for run in runs if run.metrics for k in run.metrics})

    if long:
        table = PrettyTable(["run", *varying, "launched", "status", *metric_keys])
        table.align = "l"
        for run in runs:
            cfg = all_cfg[run.signature]
            launched = run.launched_on[:16].replace("T", " ")
            status = run.status
            metrics = run.metrics or {}
            table.add_row([
                run.signature,
                *[cfg.get(k, "—") for k in varying],
                launched,
                status,
                *[metrics.get(k, "—") for k in metric_keys],
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
                *[metrics.get(k, "—") for k in metric_keys],
            ])

    return table


def build_grid_table(results: list[GridRun]) -> PrettyTable:
    """Build a PrettyTable summarising a grid run's outcomes."""
    parsed = [parse_overrides(r.overrides) for r in results]

    all_keys = sorted({k for d in parsed for k in d})
    varying = [k for k in all_keys if len({d.get(k) for d in parsed}) > 1]
    columns = varying if varying else all_keys

    table = PrettyTable(["overrides", "outcome"])
    table.align = "l"
    for result, cfg in zip(results, parsed):
        table.add_row([short_config_str(cfg, columns), result.outcome])
    return table


# ---------------------------------------------------------------------------
# Text printers
# ---------------------------------------------------------------------------

def print_config_matches(matches: list[Selection]) -> int:
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


def print_summary_matches(matches: list[Selection], *, xps_only: bool) -> None:
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
                status = run.status
                metrics_str = f"  {run.metrics}" if run.metrics else ""
                print(f"  - {run.signature}  [{status}]{metrics_str}")
                print(f"    path: {run.path}")


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
