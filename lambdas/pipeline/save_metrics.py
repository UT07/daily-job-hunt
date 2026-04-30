import logging
import os
import uuid
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")
cloudwatch = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "eu-west-1"))

# CloudWatch namespace + metric names — paired with the alarms in template.yaml
# (DailyPipelineNoArtifactsAlarm in particular). If you rename either side,
# rename both or the alarm goes silent.
_METRICS_NAMESPACE = "Naukribaba/Pipeline"
_METRIC_ARTIFACTS_COMPILED = "ArtifactsCompiled"
_METRIC_JOBS_MATCHED = "JobsMatched"
_METRIC_PIPELINE_RUN = "PipelineRun"


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def _count_compiled_artifacts(processed_jobs):
    """Count jobs whose tailor → compile → save chain produced a real PDF.

    Each entry in processed_jobs is the merged Map iterator state, which
    includes `compile_result.pdf_s3_key` if the resume PDF compiled. Returns
    a dict with separate counts for resumes and cover letters so the metric
    we emit isn't muddled.
    """
    if not processed_jobs:
        return {"resumes": 0, "cover_letters": 0}
    resumes = 0
    cover_letters = 0
    for job in processed_jobs:
        if not isinstance(job, dict):
            continue
        compile_result = job.get("compile_result") or {}
        if compile_result.get("pdf_s3_key"):
            resumes += 1
        cover_compile = job.get("cover_compile_result") or {}
        if cover_compile.get("pdf_s3_key"):
            cover_letters += 1
    return {"resumes": resumes, "cover_letters": cover_letters}


def _emit_cloudwatch_metrics(counts, matched_count):
    """Push a single PutMetricData call. Best-effort — exceptions are logged
    but don't fail the pipeline; the DB write is the load-bearing path.
    """
    try:
        cloudwatch.put_metric_data(
            Namespace=_METRICS_NAMESPACE,
            MetricData=[
                {
                    "MetricName": _METRIC_PIPELINE_RUN,
                    "Value": 1,
                    "Unit": "Count",
                },
                {
                    "MetricName": _METRIC_JOBS_MATCHED,
                    "Value": matched_count,
                    "Unit": "Count",
                },
                {
                    "MetricName": _METRIC_ARTIFACTS_COMPILED,
                    "Value": counts["resumes"],
                    "Unit": "Count",
                    "Dimensions": [{"Name": "DocType", "Value": "resume"}],
                },
                {
                    "MetricName": _METRIC_ARTIFACTS_COMPILED,
                    "Value": counts["cover_letters"],
                    "Unit": "Count",
                    "Dimensions": [{"Name": "DocType", "Value": "cover_letter"}],
                },
            ],
        )
    except Exception as e:
        logger.warning(f"[save_metrics] PutMetricData failed: {e}")


def handler(event, context):
    user_id = event["user_id"]
    scraper_results = event.get("scraper_results", [])
    score_result = event.get("score_result", {})
    dedup_result = event.get("dedup_result", {})
    processed_jobs = event.get("processed_jobs") or []

    db = get_supabase()
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    # Save per-scraper metrics
    for result in scraper_results:
        db.table("pipeline_metrics").insert({
            "user_id": user_id,
            "run_date": today,
            "scraper_name": result.get("source", "unknown"),
            "jobs_found": result.get("count", 0),
            "apify_cost_cents": result.get("apify_cost_cents", 0),
            "error_message": result.get("error"),
        }).execute()

    # Save summary run record to `runs` table so dashboard shows latest status
    total_found = sum(r.get("count", 0) for r in scraper_results if not r.get("skipped"))
    total_new = dedup_result.get("total_new", 0) if isinstance(dedup_result, dict) else 0
    matched_count = score_result.get("matched_count", 0) if isinstance(score_result, dict) else 0
    counts = _count_compiled_artifacts(processed_jobs)

    try:
        db.table("runs").insert({
            "run_id": str(uuid.uuid4()),
            "user_id": user_id,
            "run_date": today,
            "run_time": now.strftime("%H:%M:%S"),
            "raw_jobs": total_found,
            "unique_jobs": total_new,
            "matched_jobs": matched_count,
            "resumes_generated": counts["resumes"],
            "status": "completed",
            "completed_at": now.isoformat(),
        }).execute()
    except Exception as e:
        logger.warning(f"[save_metrics] Failed to insert run record: {e}")

    # Emit CloudWatch metrics — paired with template.yaml's
    # DailyPipelineNoArtifactsAlarm (B.5). Without this, runs that succeed
    # but produce 0 PDFs (today's empty-tectonic-layer pattern) go silent.
    _emit_cloudwatch_metrics(counts, matched_count)

    logger.info(
        f"[save_metrics] Saved {len(scraper_results)} scraper metrics + run summary "
        f"(found={total_found}, new={total_new}, matched={matched_count}, "
        f"resumes={counts['resumes']}, cover_letters={counts['cover_letters']})"
    )
    return {
        "saved": len(scraper_results),
        "resumes_compiled": counts["resumes"],
        "cover_letters_compiled": counts["cover_letters"],
    }
