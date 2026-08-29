# For the next Cursor agent

You are continuing work on **adtech-finacne-databricks**. Read `agent/README.md`, then `PLAN.md`, `DONE.md`, `CONTEXT.md`, `DECISIONS.md`. Append to `LOG.md` when you finish a task.

## Goal the user cares about

Simulate AdTech + finance data as if it arrived from **many real company systems**, then ingest it in **Databricks**. Notebook **03** generates events. Notebook **04** turns each topic into vendor-shaped landings. Bronze jobs should read **`sources/`**, not the raw generator dump.

## Git

- Branch: `cursor/local-workspace-and-landing-cf00`
- Default GitHub branch is still `develop` (older; missing 04 and `generator_src/`).
- Prefer committing **this `agent/` folder** with every meaningful change.
- Start Cloud Agents **from this GitHub repo**, not an empty workspace, or you cannot push.
- Never ask the user to paste a PAT in chat if Cursor GitHub auth can be used. A PAT was leaked earlier; it must stay revoked.

## Do not

- Do not change `03_run_generator.ipynb` unless the user asks. Multi-source is 04’s job.
- Do not ingest `adtech-raw` / `data/landing` as production sources.
- Do not commit generated JSON under `data/landing` or `data/sources`.

## After you change code

1. Update `DONE.md` / `PLAN.md` if status changed.
2. Append a dated entry to `LOG.md`.
3. Commit on `cursor/local-workspace-and-landing-cf00`.
4. Push if credentials exist.
5. User pulls that branch in Databricks Repos.
