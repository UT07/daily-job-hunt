"""Backfill missing resumes + cover letters for S+A tier jobs.

Invokes Lambda functions directly — skips scraping and scoring.
Only runs: TailorResume → CompileResume → GenerateCoverLetter → CompileCoverLetter → SaveJob
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3

REGION = "eu-west-1"
USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"
MAX_WORKERS = 2  # Keep low — each job invokes 5 Lambdas sequentially
DELAY_BETWEEN_JOBS = 3  # seconds between starting new jobs

lambda_client = boto3.client("lambda", region_name=REGION)


def invoke_lambda(function_name: str, payload: dict, max_retries: int = 3) -> dict:
    """Invoke a Lambda function with retry on rate limiting."""
    for attempt in range(max_retries + 1):
        try:
            resp = lambda_client.invoke(
                FunctionName=function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(payload),
            )
            body = json.loads(resp["Payload"].read())
            if resp.get("FunctionError"):
                return {"error": body}
            return body
        except lambda_client.exceptions.TooManyRequestsException:
            if attempt < max_retries:
                wait = (2 ** attempt) * 5  # 5s, 10s, 20s
                time.sleep(wait)
            else:
                raise


def process_job(job_hash: str, light_touch: bool) -> dict:
    """Run the full tailor → compile → cover letter → compile → save chain for one job."""
    result = {"job_hash": job_hash, "steps": {}}

    # Step 1: Tailor resume
    tailor = invoke_lambda("naukribaba-tailor-resume", {
        "job_hash": job_hash, "user_id": USER_ID, "light_touch": light_touch,
    })
    result["steps"]["tailor"] = "ok" if "tex_s3_key" in str(tailor) else f"FAIL: {str(tailor)[:100]}"
    if "error" in tailor or "tex_s3_key" not in str(tailor):
        result["status"] = "tailor_failed"
        return result

    tex_key = tailor.get("tex_s3_key", "")

    # Step 2: Compile resume
    compile_res = invoke_lambda("naukribaba-compile-latex", {
        "tex_s3_key": tex_key, "job_hash": job_hash, "user_id": USER_ID, "doc_type": "resume",
    })
    result["steps"]["compile_resume"] = "ok" if "pdf_s3_key" in str(compile_res) else f"FAIL: {str(compile_res)[:100]}"

    # Step 3: Generate cover letter
    cover = invoke_lambda("naukribaba-generate-cover-letter", {
        "job_hash": job_hash, "user_id": USER_ID, "light_touch": light_touch,
    })
    result["steps"]["cover_letter"] = "ok" if "tex_s3_key" in str(cover) else f"FAIL: {str(cover)[:100]}"

    cover_tex_key = cover.get("tex_s3_key", "") if isinstance(cover, dict) else ""

    # Step 4: Compile cover letter
    if cover_tex_key:
        compile_cl = invoke_lambda("naukribaba-compile-latex", {
            "tex_s3_key": cover_tex_key, "job_hash": job_hash, "user_id": USER_ID, "doc_type": "cover_letter",
        })
        result["steps"]["compile_cl"] = "ok" if "pdf_s3_key" in str(compile_cl) else f"FAIL: {str(compile_cl)[:100]}"

    # Step 5: Save job (generates presigned URLs, sets status)
    save_event = {"job_hash": job_hash, "user_id": USER_ID}
    if "pdf_s3_key" in str(compile_res):
        save_event["compile_result"] = compile_res
    if cover_tex_key and "pdf_s3_key" in str(compile_cl):
        save_event["cover_compile_result"] = compile_cl

    save = invoke_lambda("naukribaba-save-job", save_event)
    result["steps"]["save"] = "ok" if save.get("saved") else f"FAIL: {str(save)[:100]}"
    result["status"] = "ok" if save.get("saved") else "save_failed"

    return result


def main():
    from supabase import create_client

    ssm = boto3.client("ssm", region_name=REGION)
    url = ssm.get_parameter(Name="/naukribaba/SUPABASE_URL", WithDecryption=True)["Parameter"]["Value"]
    key = ssm.get_parameter(Name="/naukribaba/SUPABASE_SERVICE_KEY", WithDecryption=True)["Parameter"]["Value"]
    db = create_client(url, key)

    # Get S+A jobs missing resumes
    need = db.table("jobs").select("job_hash, match_score, score_tier") \
        .in_("score_tier", ["S", "A"]).is_("resume_s3_url", "null").execute()

    jobs = [(j["job_hash"], j["match_score"] >= 85) for j in need.data]
    print(f"Backfill: {len(jobs)} S+A jobs (light={sum(1 for _, lt in jobs if lt)}, full={sum(1 for _, lt in jobs if not lt)})")

    if "--dry-run" in sys.argv:
        for h, lt in jobs[:5]:
            print(f"  Would process: {h} (light_touch={lt})")
        print(f"  ... and {len(jobs)-5} more")
        return

    ok = 0
    fail = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for i, (h, lt) in enumerate(jobs):
            futures[pool.submit(process_job, h, lt)] = h
            if i > 0 and i % MAX_WORKERS == 0:
                time.sleep(DELAY_BETWEEN_JOBS)
        for future in as_completed(futures):
            job_hash = futures[future]
            try:
                result = future.result()
                status = result.get("status", "unknown")
                steps = result.get("steps", {})
                if status == "ok":
                    ok += 1
                    print(f"  ✓ {job_hash} ({ok+fail}/{len(jobs)})")
                else:
                    fail += 1
                    print(f"  ✗ {job_hash}: {status} — {steps}")
            except Exception as e:
                fail += 1
                print(f"  ✗ {job_hash}: EXCEPTION {e}")

    elapsed = time.time() - start
    print(f"\nDone: {ok} ok, {fail} failed, {elapsed:.0f}s ({elapsed/60:.1f}m)")


if __name__ == "__main__":
    main()
