# Shared workspace (this Cloud Agent VM + GitHub)

This Cloud Agent session did **not** start with a linked GitHub checkout. The repo now lives here so you, later turns, and other agents on this environment can use the same files:

**Folder:** `/agent/adtech-finacne-databricks`

GitHub: https://github.com/Jatinjangid104/adtech-finacne-databricks  
Default branch: `develop`  
Working branch: `cursor/local-workspace-and-landing-cf00`

## Layout

```
adtech-finacne-databricks/
  README.md                          # original Databricks-oriented docs
  WORKSPACE.md                       # this file
  run_local.py                       # generate JSON on this machine
  generator_src/                     # Python extracted from Databricks notebooks
    config/generator_config.yaml
    orchestrator.py
    core/  simulation/  generators/  edge_cases/  validation/
  data/landing/                      # generated source files (gitignored JSON)
  AdTech - Finance project/
    Adtech - generators/             # original Databricks notebooks (00–03)
```

## Notebook 04 (do not change 03)

`AdTech - Finance project/Adtech - generators/04_topics_to_real_sources.ipynb` reads generator dumps and writes **vendor-shaped** landings under `data/sources/` (Kafka envelopes, object-store logs, SFTP CSV, Stripe-like API).

```bash
python3 run_local.py --iterations 1          # still the producer (same as 03)
python3 run_source_adapter.py                # 04: topics → real sources
```

Downstream jobs must ingest `data/sources/`, not `data/landing/`.

## Generate files locally

```bash
cd /agent/adtech-finacne-databricks
python3 -m pip install numpy==1.26.4 pyyaml==6.0.1 jsonschema==4.21.1
python3 run_local.py --iterations 1
```

JSON lands under `data/landing/<event_type>/dt=.../hr=.../`.

Continuous stream:

```bash
python3 run_local.py --iterations 0
```

## Databricks

Still use notebooks `00_install_deps` → `01_write_modules` → `02_write_config` → `03_run_generator` in order. Those notebooks write modules to `/Workspace/Users/<email>/adtech/`. Prefer editing `generator_src/` here and keeping notebooks in sync.

## Other Cursor agents

1. **Same environment / this folder:** open `/agent/adtech-finacne-databricks`.
2. **A new Cloud Agent:** start it **from this GitHub repo** (not a blank workspace), otherwise it will not see these files.
3. **Your laptop:** `git clone` the repo and pull the branch after it is pushed.

If `git push` failed in this session, authenticate GitHub in Cursor (or `gh auth login`) and push `cursor/local-workspace-and-landing-cf00`.
