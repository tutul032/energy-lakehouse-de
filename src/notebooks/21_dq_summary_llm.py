# Databricks notebook source
# MAGIC %md
# MAGIC # 21_dq_summary_llm
# MAGIC Optional LLM summary of the day's data quality (task `dq_summary_llm` of
# MAGIC `daily_refresh_job`; endpoint from job parameter `dq_llm_endpoint`, default
# MAGIC `databricks-gpt-oss-120b`). With an empty `endpoint` it only prints that it skipped.
# MAGIC Otherwise it builds a short prompt from today's (Europe/Berlin) `gold_dq_daily_summary`
# MAGIC and the top 10 anomalies detected today, calls `ai_query(endpoint, prompt)` via SQL and
# MAGIC stores the text with the model name and prompt length in `gold_dq_daily_summary_text`
# MAGIC (one row per day, replaced on re-runs).
# MAGIC Never fails the job: any error is logged as a warning.

# COMMAND ----------

import os
import sys

import pandas as pd

# Make src/ importable so the notebook reuses the tested package code.
sys.path.append(os.path.abspath(".."))

from energy_lakehouse import anomalies, forecast
from energy_lakehouse.transforms import (
    GOLD_DQ_DAILY_SUMMARY_TEXT_COLUMNS,
    GOLD_DQ_DAILY_SUMMARY_TEXT_COMMENT,
)
from energy_lakehouse.utils import table_name

spark.conf.set("spark.sql.session.timeZone", "UTC")

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "energy_dev")
dbutils.widgets.text("endpoint", "")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
endpoint = dbutils.widgets.get("endpoint").strip()


def fqn(table: str) -> str:
    return table_name(catalog, schema, table)


# COMMAND ----------

if not endpoint:
    print("SKIPPED: no endpoint set (job parameter dq_llm_endpoint); no LLM summary written")
else:
    try:
        today = spark.sql(f"SELECT {anomalies.LOCAL_TODAY_SQL} AS d").first()["d"]
        summary = spark.sql(
            anomalies.summary_for_day_sql(fqn("gold_dq_daily_summary"), today)
        ).collect()
        top = spark.sql(anomalies.top_anomalies_sql(fqn("gold_dq_anomalies"), today)).collect()
        if not summary:
            raise RuntimeError(f"no gold_dq_daily_summary rows for {today}")
        prompt = anomalies.llm_prompt(
            today, [r.asDict() for r in summary], [r.asDict() for r in top]
        )
        print(prompt)
        text = spark.sql(anomalies.ai_query_sql(endpoint, prompt)).first()["summary_text"]
        if not text or not text.strip():
            raise RuntimeError(f"endpoint {endpoint} returned an empty summary")

        text_table = fqn("gold_dq_daily_summary_text")
        spark.sql(
            forecast.create_table_sql(
                text_table, GOLD_DQ_DAILY_SUMMARY_TEXT_COLUMNS, GOLD_DQ_DAILY_SUMMARY_TEXT_COMMENT
            )
        )
        row = pd.DataFrame(
            {
                "detection_date": [today],
                "model": [endpoint],
                "prompt": [prompt],
                "prompt_chars": [len(prompt)],
                "summary_text": [text.strip()],
                "generated_at": [pd.Timestamp.now(tz="UTC").tz_localize(None)],
            }
        )
        spark.createDataFrame(
            row, forecast.spark_schema(GOLD_DQ_DAILY_SUMMARY_TEXT_COLUMNS)
        ).createOrReplaceTempView(anomalies.SUMMARY_VIEW)
        spark.sql(anomalies.replace_summary_sql(text_table, today))
        print(f"OK: summary for {today} written to {text_table}:\n{text.strip()}")
    except Exception as exc:  # never fail the job for the optional summary
        print(f"WARNING: LLM summary skipped: {type(exc).__name__}: {exc}")
