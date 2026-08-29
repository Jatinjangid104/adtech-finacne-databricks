# Databricks: get the repo and run it

## 1. Import from GitHub

1. Databricks → **Repos** / Git folders → Add repo.
2. URL: `https://github.com/Jatinjangid104/adtech-finacne-databricks`
3. Branch: **`cursor/local-workspace-and-landing-cf00`** (not `develop` until merged).
4. Attach a cluster (or serverless).

## 2. Run in this order (same cluster)

Folder: `AdTech - Finance project/Adtech - generators/`

| # | Notebook | Success looks like |
|---|---|---|
| 1 | `00_install_deps` | Package versions printed |
| 2 | `01_write_modules` | Files under `.../adtech/generator_src/` |
| 3 | `02_write_config` | YAML written; “go run Notebook 03” |
| 4 | `03_run_generator` | JSON under `.../adtech/adtech-raw/` (`max_iterations=1` first) |
| 5 | `04_topics_to_real_sources` | Files under `.../adtech/sources/` |

03 restarts Python in the first cell — use **Run all**. Leave `RUN_STREAMING_ADAPTER = False` in 04 unless 03 is looping forever.

## 3. Ingest like a company

Read **`.../adtech/sources/`**, one job per prefix (own checkpoint). Examples in notebook 04 markdown. Do not Auto Loader `adtech-raw` for bronze.

## 4. After Cursor pushes

In the Databricks repo: **Pull**. Re-run 01 if Python changed, then 03, then 04.
