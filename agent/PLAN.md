# Plan (what to do)

Highest value first. Check items off in `DONE.md` and add a `LOG.md` line when finished.

## Now (user already asked)

1. **Portable Databricks paths**  
   Stop hardcoding `/Workspace/Users/jatin.jangid.104@gmail.com/adtech/`.  
   Use `current_user()` (or a widget `adtech_root`) and one root:

   ```text
   /Workspace/Users/<you>/adtech/
     generator_src/
     landing/          # was adtech-raw (03)
     sources/          # 04 vendor puts
     sim_labels/
     checkpoints/
   ```

   Shared notebook `00_set_workspace.ipynb` + `%run ./00_set_workspace` at the top of 01–04 (after any `restartPython()`).

2. **Databricks how-to in-repo**  
   `DATABRICKS.md` exists. Optionally add a markdown notebook `GETTING_STARTED.ipynb` in `AdTech - Finance project/Adtech - generators/` that only displays the run order (no logic).

3. **Push `agent/` to GitHub**  
   This folder must be on the remote so other agents and Databricks Git see it. Needs GitHub auth on the agent.

## Next (pipeline that looks like a company)

4. **Bronze ingest notebooks** (one job per source, own checkpoint):  
   DSP bids, SSP impressions, click stream, click late dump, MMP CSV, CDC, invoices, GL, Stripe refunds.

5. **Silver**  
   Unify IDs, watermarks (different per source), SCD2 campaigns from CDC.

6. **Gold**  
   Spend vs invoice vs GL, refunds vs conversions, optional fraud view using `sim_labels/`.

7. **Cadence (optional)**  
   04 still republishes whatever 03 already wrote. Later: 03/orchestrator emit finance/MMP less often so it feels like T+1 files.

## Hygiene

8. Keep `generator_src/` and `01_write_modules.ipynb` in sync when editing Python.  
9. Merge this feature branch into `develop` when the user wants a stable Databricks default.  
10. Do not store PATs. Rotate if any token was pasted in chat.
