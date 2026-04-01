import logging
import os
import subprocess
import tempfile

import boto3

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
            result = subprocess.run(
                [tectonic_path, "-X", "compile", tex_path],
                capture_output=True, text=True, timeout=45,
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

            # Upload PDF to S3
            pdf_key = tex_s3_key.replace(".tex", ".pdf")
            with open(pdf_path, "rb") as f:
                s3.put_object(Bucket=bucket, Key=pdf_key, Body=f.read(), ContentType="application/pdf")

            logger.info(f"[compile] {doc_type} PDF: {pdf_key}")
            return {"job_hash": job_hash, "pdf_s3_key": pdf_key, "user_id": user_id, "doc_type": doc_type}

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
