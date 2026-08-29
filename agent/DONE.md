# Done (what already shipped)

## Product understanding

- Repo is a **simulator**, not a lakehouse pipeline.
- Eight event types mapped to real vendors (DSP, SSP, click tracker, MMP, CDC, billing, ERP, payments).
- Chaos engine: fraud, late data, duplicates, schema evolution, clock skew, hot partitions, rounding.

## Git / local run

- Branch `cursor/local-workspace-and-landing-cf00` (pushed at least through notebook 04).
- `generator_src/` extracted from Databricks notebook 01 so Python runs without Databricks.
- `run_local.py` — same role as notebook 03. Smoke test: 1 iteration, ~341 records.
- `run_source_adapter.py` — same role as notebook 04. Republished all 8 topics locally.
- `requirements.txt`, `.gitignore` for generated JSON.

## Databricks notebooks

| Notebook | Status |
|---|---|
| 00_install_deps | Original; still used |
| 01_write_modules | Original; still writes Workspace modules |
| 02_write_config | Original |
| 03_run_generator | Original producer; **do not change** unless asked |
| 04_topics_to_real_sources | **New** — topics → Kafka / object store / SFTP / CDC / API |

## Multi-source (without changing 03)

- `generator_src/source_adapter.py` maps each topic to a vendor envelope.
- Strips simulator-only `_chaos` fields into `sim_labels/`.
- Output layout under `data/sources/` locally / `.../adtech/sources/` on Databricks.

## Context for agents

- This `agent/` folder (plan, done, log, paths, Databricks runbook).
- Older `agent-log/` on this VM points here.

## Explicitly not done

- Parameterized Workspace paths (`current_user()`).
- Bronze/silver/gold Spark jobs.
- Databricks actually executed in the Cloud Agent (no workspace attached).
- `agent/` folder on GitHub until this commit is pushed.
- User’s Windows `D:` — this VM cannot write there.
