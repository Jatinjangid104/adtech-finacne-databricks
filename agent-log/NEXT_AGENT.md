# Instructions for the next agent

Read these files **in order** before changing code:

1. `CONTEXT.md` — what this repo is and what already shipped
2. `TIMELINE.md` — what we did in this Cloud Agent session
3. `PATHS.md` — folders on this VM, GitHub, and Databricks
4. `DECISIONS.md` — why things are shaped this way

## Repo and branch

- GitHub: `https://github.com/Jatinjangid104/adtech-finacne-databricks`
- Default remote branch historically: `develop`
- **Work on:** `cursor/local-workspace-and-landing-cf00` (already pushed)
- This machine clone: `/agent/e/adtech-finacne-databricks`
- Earlier clone (same commits): `/agent/adtech-finacne-databricks`

Prefer working in **`/agent/e/adtech-finacne-databricks`** so logs and code stay together.

## User intent (current)

The user wants to:

1. Simulate AdTech + finance data as if it came from **many real company sources** (not one dump).
2. Keep Databricks notebook **03 unchanged** as the producer.
3. Use notebook **04** to republish topics into vendor-shaped landings.
4. Get the code **into Databricks** via GitHub.
5. Keep using **Cursor** for further changes.
6. Have a **folder + log** so any later agent has full context (this directory).

They asked (not finished in this session) for a dedicated “how to run in Databricks” walkthrough and **portable folder paths** (stop hardcoding `jatin.jangid.104@gmail.com`). Do that next if they still want it.

## Do not

- Do not paste or reuse GitHub PATs from chat. A token was posted in the previous session; assume it is **compromised**. User should revoke it at https://github.com/settings/tokens
- Do not start Cloud Agents on an **empty** workspace if they need `git push`.
- Do not point Databricks Auto Loader at generator dump `adtech-raw` / `data/landing` for “company” ingest. Use **`sources/`** after notebook 04.
- Do not change notebook 03 unless the user explicitly asks; 04 is the adapter.

## After you change code

1. Commit on `cursor/local-workspace-and-landing-cf00`
2. Append a dated note to `TIMELINE.md` in this folder
3. Push if GitHub credentials exist
4. User pulls that branch in Databricks Repos
