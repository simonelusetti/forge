from __future__ import annotations

import hydra
from omegaconf import DictConfig

from forge import start_run


@hydra.main(version_base=None, config_path="conf", config_name="config")
def main(cfg: DictConfig) -> int:
    run = start_run(cfg)

    epochs = int(cfg.train.get("epochs", 1))
    steps_per_epoch = int(cfg.runtime.get("steps_per_epoch", 1))
    loss = None
    print("\nStarting training...")
    for epoch in range(1, epochs + 1):
        for step in range(1, steps_per_epoch + 1):
            loss = 1.0 / (epoch * step)
            global_step = (epoch - 1) * steps_per_epoch + step
            run.push_log({"loss": loss}, step=global_step)
        print(f"epoch {epoch}/{epochs}  loss={loss:.4f}")

    run.finish({"loss": loss, "epochs": epochs})
    return 0


if __name__ == "__main__":
    main()
