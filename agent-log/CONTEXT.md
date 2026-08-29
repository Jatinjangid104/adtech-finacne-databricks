# Project context (read this first)

## What this repository is

**Name:** `adtech-finacne-databricks` (typo “finacne” is the GitHub name; leave it).  
**Owner:** Jatinjangid104  
**URL:** https://github.com/Jatinjangid104/adtech-finacne-databricks  

It is **not** a full Spark pipeline. It is a **synthetic event generator** for AdTech + finance, meant to run in **Databricks** (notebooks) and also **locally** (`run_local.py`). Downstream Databricks jobs should ingest the files as if they came from real vendors.

Default GitHub branch is **`develop`**. Current work is on **`cursor/local-workspace-and-landing-cf00`**.

## Event streams the generator produces

| Folder / topic | Real-world analogue | Typical arrival | Streaming? |
|---|---|---|---|
| `bid_requests` | DSP (e.g. Google DV360) | Kafka | Yes |
| `impressions` | SSP / ad server / CDN logs | Object storage, 1–15 min | Usually no (micro-batch) |
| `clicks` | Click tracker | Kafka + delayed log dump | Hybrid |
| `conversions` | MMP (AppsFlyer etc.) | Webhook + daily SFTP/CSV | Mostly batch |
| `campaign_cdc` | Campaign DB | Debezium / Kafka CDC | Yes, low volume |
| `billing_records` | Ad-network invoice | Daily CSV/Parquet T+1 | No |
| `ledger_entries` | ERP / GL | Nightly extract T+1/T+2 | No |
| `refunds` | PSP / chargebacks | API or delayed files | No |

Chaos already in the generator: fraud, late events, duplicates, schema evolution V1–V8, clock skew, hot partitions, rounding, overbilling.

## Two layers (do not mix them)

1. **Producer (notebook 03 / `run_local.py`)**  
   Writes JSON under a single dump tree (`adtech-raw` on Databricks, `data/landing` locally). Looks like a simulator.

2. **Company put (notebook 04 / `run_source_adapter.py`)**  
   Reads that dump **without changing 03**. Writes vendor-shaped landings under `sources/`:
   - Kafka envelopes (topic, partition, offset, key, value)
   - Object-store / CDN logs
   - SFTP CSV (MMP, invoices, GL)
   - Stripe-like API JSON for refunds
   - `sim_labels/` keeps `_time_chaos_*` style fields out of bronze

**Bronze ingest must read `sources/`, not the generator dump.**

## Databricks notebooks (import this folder)

Path in repo:

`AdTech - Finance project/Adtech - generators/`

| File | Role |
|---|---|
| `00_install_deps.ipynb` | pip: numpy, pyyaml, jsonschema |
| `01_write_modules.ipynb` | Writes Python package to Workspace `generator_src/` |
| `02_write_config.ipynb` | Writes `generator_config.yaml` + injector patch |
| `03_run_generator.ipynb` | Orchestrator. Smoke test uses `max_iterations=1` |
| `04_topics_to_real_sources.ipynb` | Topic → vendor landings |

**Run order:** 00 → 01 → 02 → 03 → 04. Do not skip. After 00, Python restart is normal; 03 already calls `restartPython()`.

## Local Python (this VM)

```text
generator_src/          extracted from notebook 01
run_local.py            same job as 03
run_source_adapter.py   same job as 04
data/landing/           03 output (gitignored JSON)
data/sources/           04 output (gitignored)
```

```bash
cd /agent/e/adtech-finacne-databricks   # or /agent/adtech-finacne-databricks
pip install -r requirements.txt
python3 run_local.py --iterations 1
python3 run_source_adapter.py
```

## Hardcoded Databricks paths (known debt)

Notebooks 01–04 still contain:

`/Workspace/Users/jatin.jangid.104@gmail.com/adtech/...`

If the Databricks login email differs, those strings must be changed (or parameterized). User asked for portable paths; **not implemented yet**.

03 dump: `.../adtech/adtech-raw/`  
04 sources: `.../adtech/sources/`  
Modules: `.../adtech/generator_src/`

## Cursor / Git

- This Cloud Agent started **without** a linked repo; code was cloned from public GitHub, then extracted and committed.
- Push succeeded once with a user-supplied PAT. **Revoke that token.** Do not log tokens in `agent-log`.
- Further Cursor work: open this GitHub repo (not an empty workspace), branch `cursor/local-workspace-and-landing-cf00`.
- Databricks Git: add the GitHub URL, checkout that branch, pull after each push.

## Owner contact in notebooks

Email used in paths/grants: `jatin.jangid.104@gmail.com`
