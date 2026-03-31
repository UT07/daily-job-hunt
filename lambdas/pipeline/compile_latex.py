import logging
import os
import subprocess
import tempfile

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


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

        # Compile with tectonic
        result = subprocess.run(
            ["tectonic", "-X", "compile", tex_path],
            capture_output=True, text=True, timeout=45
        )

        if result.returncode != 0:
            logger.error(f"[compile] tectonic failed: {result.stderr}")
            return {"error": "compilation_failed", "stderr": result.stderr[:500]}

        pdf_path = os.path.join(tmpdir, "document.pdf")
        if not os.path.exists(pdf_path):
            return {"error": "no_pdf_output"}

        # Upload PDF to S3
        pdf_key = tex_s3_key.replace(".tex", ".pdf")
        with open(pdf_path, "rb") as f:
            s3.put_object(Bucket=bucket, Key=pdf_key, Body=f.read(), ContentType="application/pdf")

    logger.info(f"[compile] {doc_type} PDF: {pdf_key}")
    return {"job_hash": job_hash, "pdf_s3_key": pdf_key, "user_id": user_id, "doc_type": doc_type}
