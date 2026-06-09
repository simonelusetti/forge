# forge

Minimal experiment management for Python projects using [Hydra](https://hydra.cc/) and [OmegaConf](https://omegaconf.readthedocs.io/).

Forge tracks experiments by hashing their config, stores run metadata and metrics on disk, and provides a CLI to query, compare, and manage results — without requiring a server or database.

## Installation

```bash
pip install -e /path/to/forge
```

Tab completion (bash/zsh):

```bash
echo 'eval "$(register-python-argcomplete forge)"' >> ~/.bashrc
source ~/.bashrc
```

## Project layout

Forge expects a `conf/` directory with a Hydra config and a `train.py` (or any entry-point module) at the root of your project:

```
myproject/
  conf/
    config.yaml
    runtime.yaml   # optional runtime config
  train.py
  outputs/         # created automatically
```

## Quickstart

**1. Call `start_run` in your training script:**

```python
from forge import start_run
from omegaconf import DictConfig
import hydra

@hydra.main(version_base=None, config_path="conf", config_name="config")
def main(cfg: DictConfig) -> int:
    run = start_run(cfg)           # registers the run, chdirs into its output dir

    # ... training loop ...
    for epoch in range(cfg.train.epochs):
        loss = train_one_epoch()
        run.push_log({"loss": loss}, step=epoch)

    run.finish({"loss": loss})     # marks run as done, persists final metrics
    return 0
```

**2. Run an experiment:**

```bash
cd myproject
forge run
forge run model.optim.lr=0.01 train.epochs=10
```

Each run is stored under `outputs/xps/<experiment-sig>/<run-id>/` and contains:

| File | Contents |
|---|---|
| `meta.json` | `launched_on`, `finished_on`, `status` |
| `runtime.yaml` | Runtime config snapshot |
| `logs.jsonl` | Per-step metrics written by `push_log` |
| `metrics.json` | Final metrics written by `finish` |

## `forge.store` config key

The `forge` section of your config controls behaviour:

```yaml
forge:
  store: null        # path to outputs dir; defaults to ./outputs
  tags: null         # list of string tags attached to every run
  exclude:           # glob patterns for config keys excluded from the experiment signature hash
    - train.steps_per_epoch

```

`runtime` is stored per-run in `runtime.yaml` and does not affect the experiment signature.

## Commands

All commands support three **run-selection modes**:

| Flag | Mode | Pattern format |
|---|---|---|
| *(none)* | Override | Hydra overrides: `key=value` |
| `-S` / `--sigs` | Signature | Partial 8-char hex signature |
| `-T` / `--tags` | Tag | String tag names |

An empty pattern always matches all runs.

---

### `forge run [overrides...]`

Launch a single experiment.

```bash
forge run
forge run model.hidden_size=64 model.optim.lr=0.01
```

---

### `forge info [patterns...]`

Inspect matching experiments and their runs.

```bash
forge info                          # all experiments
forge info model.optim.lr=0.01      # experiments where lr was overridden to 0.01
forge info -S abc123                # experiment by signature prefix
forge info -T my-tag                # experiments with a specific tag
forge info --sigs-only              # print only signatures
forge info --xps-only               # skip per-run details
```

---

### `forge metrics [patterns...]`

Compare metrics across runs in a table.

```bash
forge metrics                       # all runs, compact table
forge metrics model.optim.lr=0.01
forge metrics -S abc123 --long      # full table with launched/status columns
```

The default compact view collapses varying config keys into a single `overrides` column (e.g. `lr=0.01  epochs=5`). Pass `--long` / `-l` for the full breakdown.

---

### `forge grid [globals...] [--run ...] [--sweep ...] [--file YAML]`

Launch a grid of experiments and print an outcome table.

```bash
# Cartesian product: 6 runs (3 lr × 2 epochs)
forge grid --sweep model.optim.lr=0.001,0.01,0.1 --sweep train.epochs=3,5

# Explicit runs
forge grid --run data.dataset=toy-text --run data.dataset=toy-image

# Global overrides apply to every run
forge grid model.hidden_size=64 --sweep model.optim.lr=0.001,0.01

# Load spec from a YAML file (auto-detected if first arg has no =)
forge grid sweep.yaml
forge grid --file sweep.yaml
```

**Grid YAML format:**

```yaml
globals:
  - model.hidden_size=64

direct:
  - [data.dataset=toy-text, train.epochs=1]
  - [data.dataset=toy-image, train.epochs=3]

product:
  model.optim.lr: [0.001, 0.01, 0.1]
  train.epochs: [1, 3, 5]
```

`direct` and `product` are independent — they do not cross-product with each other.

---

### `forge artifact [patterns...] <glob>`

List artifact files inside matching run directories. The last argument is always the artifact glob (relative to the run folder); everything before it selects runs.

```bash
forge artifact "*.pt"               # all .pt files in every run
forge artifact "data/*"             # contents of data/ in every run
forge artifact model.optim.lr=0.01 "checkpoints/*"
forge artifact -S abc123 "logs.jsonl"
```

---

### `forge purge [patterns...]`

Delete matching experiments or runs (with confirmation).

```bash
forge purge model.optim.lr=0.001    # delete runs matching override
forge purge -S abc123               # delete by signature
forge purge --force -S abc123       # skip confirmation
```

If deleting runs leaves an experiment with no runs, the experiment directory is also removed.

---

### `forge clean [-f]`

Delete all runs with `status = failed`.

```bash
forge clean
forge clean --force
```

---

### `forge store [patterns...]`

Archive matching experiments to a timestamped snapshot under `outputs/stored/`.

```bash
forge store                         # archive everything
forge store model.optim.lr=0.01
```

---

## Run status

Every run has a `status` field in `meta.json`:

| Status | Meaning |
|---|---|
| `running` | Set at launch |
| `done` | Set by `run.finish()` |
| `failed` | Set automatically if the process exits without calling `finish` |

The `failed` status is written by an `atexit` handler — no try/except needed in your code.

---

## Programmatic API

All CLI commands have a corresponding function importable from `forge`:

```python
from forge import (
    start_run,        # start a run from a Hydra config
    run,              # compose config + call main()
    query,            # query experiments/runs
    artifacts,        # find files inside run directories
    grid,             # launch a grid of experiments
    failed_runs,      # list all failed runs
    purge,            # delete selections
    store_targets,    # archive selections
)
```

```python
from forge import artifacts, query

# Find all checkpoint files in runs where lr=0.001
for run, files in artifacts(query(["model.optim.lr=0.001"]), "checkpoints/*.pt"):
    print(run.signature, files)
```
