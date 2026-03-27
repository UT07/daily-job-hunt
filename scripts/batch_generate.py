#!/usr/bin/env python3
"""Batch generate resume + cover letter for ALL dashboard jobs.

Usage: .venv/bin/python scripts/batch_generate.py [--skip-groq]
"""
import os, sys, json, yaml, hashlib, boto3
from pathlib import Path
from datetime import datetime

# File-based logging (stdout buffering unreliable with nohup)
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler('/tmp/batch_progress.log', mode='w'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('batch')

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env
for line in Path(__file__).parent.parent.joinpath('.env').read_text().splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

# Skip Groq if rate limited
if '--skip-groq' in sys.argv:
    os.environ.pop('GROQ_API_KEY', None)
    print("Groq disabled (--skip-groq flag)")

import requests as req
from db_client import SupabaseClient
from ai_client import AIClient
from scrapers.base import Job
from tailorer import tailor_resume
from cover_letter import generate_cover_letter
from latex_compiler import compile_tex_to_pdf

db = SupabaseClient.from_env()
url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
resp = req.get(f'{url}/auth/v1/admin/users', headers={'apikey': key, 'Authorization': f'Bearer {key}'})
user_id = resp.json()['users'][0]['id']

with open('config.yaml') as f:
    config = yaml.safe_load(f)
for k, v in config.get('api_keys', {}).items():
    if isinstance(v, str) and v.startswith('${') and v.endswith('}'):
        config['api_keys'][k] = os.environ.get(v[2:-1], '')
if '--skip-groq' in sys.argv:
    config['api_keys'].pop('groq', None)

ai_client = AIClient.from_config(config)
log.info(f"AI providers: {len(ai_client.providers)}")

s3 = boto3.client('s3')
bucket = 'utkarsh-job-hunt'
run_date = datetime.now().strftime('%Y-%m-%d')

base_fullstack = Path('resumes/fullstack.tex').read_text()
base_sre = Path('resumes/sre_devops.tex').read_text()

all_jobs = []
page = 1
while True:
    batch = db.get_jobs(user_id, page=page, per_page=50)
    if not batch:
        break
    all_jobs.extend(batch)
    page += 1

log.info(f"Total jobs: {len(all_jobs)}")

out_resumes = Path(f'output/{run_date}/resumes')
out_cl = Path(f'output/{run_date}/cover_letters')
out_resumes.mkdir(parents=True, exist_ok=True)
out_cl.mkdir(parents=True, exist_ok=True)

generated = 0
failed = 0

for i, job_row in enumerate(all_jobs):
    title = job_row.get('title', '')
    company = job_row.get('company', '')
    if not title or not company or company == 'Unknown':
        continue

    log.info(f"\n[{i+1}/{len(all_jobs)}] {title} @ {company}")

    resume_type = job_row.get('matched_resume', 'fullstack')
    base_tex = base_sre if resume_type == 'sre_devops' else base_fullstack

    job = Job(
        title=title, company=company,
        location=job_row.get('location', '') or 'Ireland',
        description=job_row.get('description', '') or f'{title} role at {company}.',
        apply_url=job_row.get('apply_url', ''),
        source=job_row.get('source', 'pipeline'),
    )

    try:
        tex_path = tailor_resume(job, base_tex, ai_client, out_resumes)
        if not tex_path:
            log.info(f"  SKIP: tailoring failed")
            failed += 1
            continue

        pdf_path = compile_tex_to_pdf(tex_path)
        if not pdf_path:
            log.info(f"  SKIP: PDF compilation failed")
            failed += 1
            continue

        pdf_filename = Path(pdf_path).name
        s3_key = f"users/{user_id}/{run_date}/resumes/{pdf_filename}"
        s3.upload_file(pdf_path, bucket, s3_key, ExtraArgs={'ContentType': 'application/pdf'})
        resume_url = s3.generate_presigned_url('get_object',
            Params={'Bucket': bucket, 'Key': s3_key}, ExpiresIn=2592000)

        cl_tex = generate_cover_letter(job, base_tex, ai_client, out_cl)
        cl_url = ''
        cl_filename = ''
        if cl_tex:
            cl_pdf = compile_tex_to_pdf(cl_tex)
            if cl_pdf:
                cl_filename = Path(cl_pdf).name
                cl_s3_key = f"users/{user_id}/{run_date}/cover_letters/{cl_filename}"
                s3.upload_file(cl_pdf, bucket, cl_s3_key, ExtraArgs={'ContentType': 'application/pdf'})
                cl_url = s3.generate_presigned_url('get_object',
                    Params={'Bucket': bucket, 'Key': cl_s3_key}, ExpiresIn=2592000)

        update = {'resume_s3_url': resume_url, 'tailored_pdf_path': pdf_filename}
        if cl_url:
            update['cover_letter_s3_url'] = cl_url
            update['cover_letter_pdf_path'] = cl_filename

        db.client.table('jobs').update(update).eq('job_id', job_row['job_id']).eq('user_id', user_id).execute()
        generated += 1
        log.info(f"  OK: resume{' + CL' if cl_url else ''} -> S3 + Supabase")

    except Exception as e:
        log.info(f"  ERROR: {e}")
        failed += 1

log.info(f"\n{'='*50}")
log.info(f"DONE: {generated}/{len(all_jobs)} generated, {failed} failed")
log.info(f"{'='*50}")
