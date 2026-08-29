# How to get this into Databricks and run it

Import **from GitHub**, branch `cursor/local-workspace-and-landing-cf00`. Do not upload random JSON from this VM unless you are debugging.

## A. Put the repo in Databricks

1. Log into Databricks.
2. **Repos** (or **Workspace → Git folders**) → **Add repo**.
3. Git URL: `https://github.com/Jatinjangid104/adtech-finacne-databricks`
4. Provider: GitHub (authenticate if asked).
5. **Branch:** `cursor/local-workspace-and-landing-cf00` (not `develop` — develop does not have notebook 04 or `generator_src/`).
6. Create / open a **cluster** (or serverless) and attach it to the notebooks.

Repo folder in Workspace will look like:

`Repos/<you>/adtech-finacne-databricks/AdTech - Finance project/Adtech - generators/`

## B. Run notebooks in order (same cluster)

| Step | Notebook | What you should see |
|---|---|---|
| 1 | `00_install_deps` | numpy / pyyaml / jsonschema versions printed |
| 2 | `01_write_modules` | Files written under `/Workspace/Users/<email>/adtech/generator_src/` |
| 3 | `02_write_config` | Config written; “Config complete — go run Notebook 03” |
| 4 | `03_run_generator` | Orchestrator; with `max_iterations=1` a small JSON dump under `.../adtech/adtech-raw/` |
| 5 | `04_topics_to_real_sources` | Vendor folders under `.../adtech/sources/` |

If your Databricks user is **not** `jatin.jangid.104@gmail.com`, edit that email in 01, 02, 03, 04 to your user **or** wait for path parameterization (not done yet). All four must use the **same** `.../Users/<email>/adtech/` root.

03 first cell restarts Python — run **all cells** after that. For a first test leave `max_iterations=1`. For a live firehose later, set `max_iterations=None`.

04 first run: leave `RUN_STREAMING_ADAPTER = False`. Re-run 04 after 03 if you generate more files.

## C. What “company ingest” reads

After 04, point Auto Loader / `spark.read` at **`.../adtech/sources/`**, for example:

- `sources/kafka/dsp_google/bid-requests`
- `sources/object_store/ssp_openx/impressions`
- `sources/sftp/mmp_appsflyer/conversions`
- `sources/sftp/billing/dv360_invoices`
- `sources/sftp/erp_netsuite/gl`
- `sources/api/payments_stripe/refunds`
- `sources/kafka/campaign_db/cdc`

Do **not** use `adtech-raw` as the production put.

## D. After Cursor changes later

Cursor pushes to GitHub → in Databricks Repo click **Pull**. Then re-run 01 if Python changed, 03 to generate, 04 to republish.

## E. Local (this VM) vs Databricks

Local: `/agent/e/adtech-finacne-databricks` + `run_local.py` / `run_source_adapter.py`.  
Databricks: notebooks 00–04. Same ideas, different paths.
