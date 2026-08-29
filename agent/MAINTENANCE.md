# How to maintain this folder

After **any** meaningful code or process change:

1. If a plan item finished → move it from `PLAN.md` to `DONE.md`.
2. If a new task appeared → add it to `PLAN.md`.
3. Always **prepend** a block to `LOG.md` (date, who, what, why, follow-up).
4. If paths changed → edit `PATHS.md`.
5. Commit `agent/` in the **same commit** as the code when possible.

Do not rewrite old LOG entries; add a new one that says you corrected something.

Other agents: do not create a second context folder (`notes/`, `docs/agent/`, etc.). Extend **`agent/`** only.
