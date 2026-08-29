# Paths

## GitHub

- https://github.com/Jatinjangid104/adtech-finacne-databricks
- Work branch: `cursor/local-workspace-and-landing-cf00`
- Older default: `develop` (no notebook 04 / `generator_src/` at the time of this log)

## Databricks (after Git add of that branch)

Notebooks:

`AdTech - Finance project/Adtech - generators/`

Hardcoded Workspace root today:

```text
/Workspace/Users/jatin.jangid.104@gmail.com/adtech/
  generator_src/     # 01
  adtech-raw/        # 03 dump
  sources/           # 04
  sim_labels/
```

If login email differs, change all four notebooks to the same user — or implement PLAN item 1.

## This Cloud Agent VM (not your laptop)

```text
/agent/adtech-finacne-databricks          primary git worktree
/agent/e/adtech-finacne-databricks        extra clone
/agent/d/adtech-finacne-databricks        Drive D stand-in copy
```

There is **no** Windows `D:` here. Local generated data: `data/landing/`, `data/sources/` (gitignored).

## Local run (any clone of this repo)

```bash
pip install -r requirements.txt
python3 run_local.py --iterations 1
python3 run_source_adapter.py
```
