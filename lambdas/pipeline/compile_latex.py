import logging
import os
import subprocess
import tempfile

import boto3

from shared.composition_policy import resolve
from shared.page_check import check_pdf, log_violations
from utils.pdf_validator import check_file_size

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# tectonic is provided via the TectonicLayer Lambda Layer, which extracts to /opt/.
# The binary is at /opt/bin/tectonic. We fall back to bare "tectonic" for local runs.


def handler(event, context):
    tex_s3_key = event["tex_s3_key"]
    job_hash = event.get("job_hash", "")
    user_id = event.get("user_id", "")
    doc_type = event.get("doc_type", "resume")

    s3 = boto3.client("s3")
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    # Read tex from S3
    obj = s3.get_object(Bucket=bucket, Key=tex_s3_key)
    tex_content = obj["Body"].read().decode("utf-8")

    # Write to temp file and compile
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "document.tex")
        with open(tex_path, "w") as f:
            f.write(tex_content)

        # Resolve tectonic binary: Lambda Layer extracts to /opt/bin/tectonic
        tectonic_path = "/opt/bin/tectonic" if os.path.exists("/opt/bin/tectonic") else "tectonic"

        try:
            # tectonic needs writable cache — Lambda only allows /tmp
            env = os.environ.copy()
            env["XDG_CACHE_HOME"] = "/tmp"
            env["HOME"] = "/tmp"

            # /tmp is empty on a cold execution environment, so XDG_CACHE_HOME
            # above starts with no tectonic bundle cache: the first compile in
            # a fresh container has to fetch the whole LaTeX package bundle
            # over the network before it can compile anything. 45s was only
            # ever enough for a warm container with the bundle already
            # cached, and this stack can go idle for weeks between
            # invocations (e.g. the pipeline being parked), so a cold
            # container is the common case, not the edge case. 110s covers a
            # cold fetch + compile; CompileLatexFunction's own Timeout in
            # template.yaml is raised alongside this so the Lambda outlives
            # the subprocess instead of being hard-killed first.
            result = subprocess.run(
                [tectonic_path, "-X", "compile", tex_path],
                capture_output=True, text=True, timeout=110,
                env=env,
            )
            if result.returncode != 0:
                logger.error(f"[compile] tectonic failed: {result.stderr}")
                return {"error": "compilation_failed", "stderr": result.stderr[:500],
                        "tex_s3_key": tex_s3_key, "job_hash": job_hash,
                        "user_id": user_id, "doc_type": doc_type}

            pdf_path = os.path.join(tmpdir, "document.pdf")
            if not os.path.exists(pdf_path):
                return {"error": "no_pdf_output", "tex_s3_key": tex_s3_key,
                        "job_hash": job_hash, "user_id": user_id, "doc_type": doc_type}

            # --- Page check: the rule that only the compiled PDF can answer ---
            #
            # This replaces a call to utils.pdf_validator.validate_pdf that had
            # never run. It needs `fitz` (pymupdf), which is in no requirements
            # file in this repo, so every invocation took the ImportError path,
            # logged "PDF validation skipped" at warning level and returned
            # valid=True. Its page-count check has therefore never once
            # executed in production, which is why a resume with a blank page
            # in the middle cleared this function.
            #
            # shared.page_check uses pdfplumber (requirements.txt,
            # requirements-web.txt, and layer/requirements.txt so it is on this
            # zip Lambda's path) and reports a violation when it cannot run,
            # rather than reporting success.
            #
            # `pages` is not threaded through either state machine, so this
            # falls back to composition_policy's default of 2 — which is what
            # every user has today. Passing `pages` in the event overrides it
            # for when a per-user policy does get threaded through.
            expected_pages = event.get("pages")
            if expected_pages is None and doc_type == "resume":
                expected_pages = resolve(None)["pages"]
            page_violations = check_pdf(pdf_path, expected_pages=expected_pages)
            log_violations(page_violations, label=f"{doc_type} {job_hash}")

            size_issue = check_file_size(os.path.getsize(pdf_path))
            if size_issue:
                logger.warning(f"[compile] PDF file size for {job_hash}: {size_issue}")

            # Upload PDF to S3
            pdf_key = tex_s3_key.replace(".tex", ".pdf")
            with open(pdf_path, "rb") as f:
                s3.put_object(Bucket=bucket, Key=pdf_key, Body=f.read(), ContentType="application/pdf")

            logger.info(f"[compile] {doc_type} PDF: {pdf_key}")
            # page_violations is always present: an empty list is the claim that
            # the PDF was measured and complies. A missing key would be
            # indistinguishable from a check that never ran — the exact failure
            # this block replaces.
            return {"job_hash": job_hash, "pdf_s3_key": pdf_key, "user_id": user_id,
                    "doc_type": doc_type, "page_violations": page_violations}

        except FileNotFoundError:
            # tectonic binary not available in this runtime
            logger.warning("[compile] tectonic not available - returning tex key only (no PDF compiled)")
            return {
                "job_hash": job_hash,
                "pdf_s3_key": None,
                "tex_s3_key": tex_s3_key,
                "user_id": user_id,
                "doc_type": doc_type,
                "error": "tectonic_not_available",
            }
