# Paths

## This Cloud Agent VM

```text
/agent/e/README.md
/agent/e/adtech-finacne-databricks/     ← preferred clone (this tree)
  agent-log/                           ← read NEXT_AGENT.md first
  AdTech - Finance project/Adtech - generators/   ← Databricks notebooks
  generator_src/
  data/landing/                        ← local 03 output (gitignored)
  data/sources/                        ← local 04 output (gitignored)
  run_local.py
  run_source_adapter.py

/agent/adtech-finacne-databricks/       ← earlier clone; keep in sync after commits
```

## GitHub

- Repo: `Jatinjangid104/adtech-finacne-databricks`
- Branch to use: `cursor/local-workspace-and-landing-cf00`
- Tree: https://github.com/Jatinjangid104/adtech-finacne-databricks/tree/cursor/local-workspace-and-landing-cf00

## Databricks (after Git repo add + that branch)

Notebooks live at:

`AdTech - Finance project/Adtech - generators/`

Runtime Workspace paths **hardcoded today**:

```text
/Workspace/Users/jatin.jangid.104@gmail.com/adtech/generator_src/
/Workspace/Users/jatin.jangid.104@gmail.com/adtech/adtech-raw/     # 03 dump
/Workspace/Users/jatin.jangid.104@gmail.com/adtech/sources/        # 04 vendor landings
/Workspace/Users/jatin.jangid.104@gmail.com/adtech/sim_labels/
```

Local equivalents:

```text
generator_src/
data/landing/     ↔ adtech-raw
data/sources/     ↔ sources
```

## Databricks Git import

1. Databricks left sidebar → **Repos** (or Git folders) → Add repo  
2. URL `https://github.com/Jatinjangid104/adtech-finacne-databricks`  
3. Branch `cursor/local-workspace-and-landing-cf00`  
4. Attach a cluster  
5. Run notebooks 00 → 01 → 02 → 03 → 04 in that folder
