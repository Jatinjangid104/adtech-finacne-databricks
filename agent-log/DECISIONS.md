# Decisions

## Keep notebook 03 as the producer

User asked to simulate multi-source **without changing 03**. Notebook 04 is a **republish adapter**. Downstream “company” jobs should not read 03’s dump.

## Generator modules live in two places

- Databricks: 01 **writes** `.py` files into Workspace at runtime (historical design).
- Git: `generator_src/` is an **extracted** copy so local/Cursor agents can run without Databricks.

If you edit Python, update **both** `generator_src/` and the corresponding cell in `01_write_modules.ipynb`, or later add a sync step. They can drift.

## Landing vs sources

- `landing` / `adtech-raw` = simulator firehose (all topics, ~5s batches). Fine for generating data.
- `sources/` = vendor-shaped put (Kafka envelope, SFTP CSV, etc.). This is what should look like production.

## Branch naming

Cloud Agent rule used: `cursor/<descriptive-name>-cf00`. Do not rename unless asked.

## Secrets

Never commit GitHub tokens. One PAT was used for a single push then must be revoked. `agent-log` must not contain token values.

## Unfinished (user still wants)

- Databricks step-by-step “walkthrough notebook/agent”
- Replace hardcoded email paths with `current_user()` / widgets and one `ADTECH_ROOT`
- Optional: `%run ./00_set_workspace` shared by 01–04
