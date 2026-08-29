# Log (append only)

Newest entries at the **top**. Format:

```text
## YYYY-MM-DD HH:MM UTC — short title
Who: agent | user
What: ...
Why: ...
Follow-up: ...
```

---

## 2026-08-29 18:45 UTC — add `agent/` folder in git repo

Who: agent  
What: Created `agent/` at repo root (README, FOR_AGENTS, PLAN, DONE, LOG, CONTEXT, PATHS, DATABRICKS, DECISIONS, MAINTENANCE). Older `agent-log/` becomes a pointer.  
Why: User asked for a git-tracked folder so any agent can maintain plan, done work, and logs.  
Follow-up: Push branch to GitHub when auth exists.

## 2026-08-29 18:38 UTC — copy to `/agent/d`

Who: agent  
What: Copied clone + logs to `/agent/d` as a Drive D stand-in. No Windows D: on this VM.  
Why: User asked to copy everything to drive D.  
Follow-up: User clones on their PC to `D:\` themselves.

## 2026-08-29 16:26 UTC — `/agent/e` clone + `agent-log/`

Who: agent  
What: `/agent/e/adtech-finacne-databricks` clone and first handoff markdown.  
Why: User wanted a shared folder and log for other agents.  
Follow-up: Superseded by `agent/` in the repo.

## 2026-08-29 16:14 UTC — pushed branch to GitHub

Who: agent (user-supplied PAT, now must be revoked)  
What: Pushed `cursor/local-workspace-and-landing-cf00` including notebook 04 and `generator_src/`.  
Why: User needed Databricks Git to see the work.  
Follow-up: Revoke PAT. Do not paste tokens in chat again.

## 2026-08-29 15:49 UTC — notebook 04 source adapter

Who: agent  
What: `04_topics_to_real_sources.ipynb`, `source_adapter.py`, `run_source_adapter.py`. Local republish of 8 topics.  
Why: Multi-source without changing notebook 03.  
Follow-up: Bronze Auto Loader jobs still missing.

## 2026-08-29 15:33 UTC — local extract and `run_local.py`

Who: agent  
What: Cloned public repo into `/agent/adtech-finacne-databricks`, extracted modules, smoke-tested generator.  
Why: Empty Cloud Agent workspace; user wanted files + later git push.  
Follow-up: First push failed (no GitHub auth).

## 2026-08-29 earlier — explain repo and multi-source design

Who: agent  
What: Explained 00–03 usage, real-company source map, streaming vs batch, landing-zone pattern.  
Why: User forgot how to use the project and wanted realistic multi-source data.
