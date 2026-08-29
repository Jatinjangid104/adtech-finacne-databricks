# Architecture context

## One-sentence summary

A **Databricks-oriented event simulator** for digital ads + finance. It writes messy, realistic JSON; notebook 04 makes those files look like **separate vendor puts**. You still need Spark jobs for bronze/silver/gold.

## Layers

```text
03 / run_local.py          producer (simulator dump)
        ↓
04 / run_source_adapter    company-shaped landings (Kafka, S3-like, SFTP, CDC, API)
        ↓
future bronze jobs         one Auto Loader / checkpoint per source
        ↓
silver / gold              joins, watermarks, recon
```

## Topics

| Topic | Pretend source | Streaming? | 04 landing (under `sources/`) |
|---|---|---|---|
| bid_requests | Google DV360 Kafka | yes | `kafka/dsp_google/bid-requests` |
| impressions | OpenX / CDN files | no | `object_store/ssp_openx/impressions` |
| clicks | Click tracker Kafka | yes | `kafka/click_tracker/clicks` |
| clicks (copy) | Late log dump | no | `object_store/click_tracker/clicks_late` |
| conversions | AppsFlyer SFTP | no | `sftp/mmp_appsflyer/conversions` |
| conversions (copy) | MMP webhook | trickle | `webhook/mmp_appsflyer/conversions` |
| campaign_cdc | Debezium Postgres | yes, small | `kafka/campaign_db/cdc` |
| billing_records | DV360 invoice | no | `sftp/billing/dv360_invoices` |
| ledger_entries | NetSuite GL | no | `sftp/erp_netsuite/gl` |
| refunds | Stripe disputes | no | `api/payments_stripe/refunds` |

## Chaos (already in 03)

Fraud/bots, late events, duplicates, schema V1–V8, clock skew, hot partitions, rounding, overbilling. 04 **strips** `_time_chaos_*` / `_financial_edge_case` from vendor files and keeps them in `sim_labels/` for later evaluation.
