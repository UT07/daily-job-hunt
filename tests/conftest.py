"""Shared test fixtures for all test tiers."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

# Add Lambda module paths so they can be imported in tests
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lambdas" / "scrapers"))
sys.path.insert(0, str(PROJECT_ROOT / "lambdas" / "pipeline"))

# Load .env for integration tests
env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


@pytest.fixture
def aws_credentials():
    """Mock AWS credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"


@pytest.fixture
def mock_ssm(aws_credentials):
    """Mock SSM with standard NaukriBaba parameters."""
    with mock_aws():
        ssm = boto3.client("ssm", region_name="eu-west-1")
        params = {
            "/naukribaba/SUPABASE_URL": "https://test.supabase.co",
            "/naukribaba/SUPABASE_SERVICE_KEY": "test-service-key",
            "/naukribaba/APIFY_API_KEY": "test-apify-key",
            "/naukribaba/ADZUNA_APP_ID": "test-adzuna-id",
            "/naukribaba/ADZUNA_APP_KEY": "test-adzuna-key",
            "/naukribaba/GROQ_API_KEY": "test-groq-key",
            "/naukribaba/NVIDIA_API_KEY": "test-nvidia-key",
            "/naukribaba/DEEPSEEK_API_KEY": "test-deepseek-key",
            "/naukribaba/OPENROUTER_API_KEY": "test-openrouter-key",
            "/naukribaba/QWEN_API_KEY": "test-qwen-key",
            "/naukribaba/GMAIL_USER": "test@gmail.com",
            "/naukribaba/GMAIL_APP_PASSWORD": "test-password",
        }
        for name, value in params.items():
            ssm.put_parameter(Name=name, Value=value, Type="SecureString")
        yield ssm


@pytest.fixture
def mock_s3(aws_credentials):
    """Mock S3 with test bucket."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-west-1")
        s3.create_bucket(
            Bucket="utkarsh-job-hunt",
            CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
        )
        yield s3


@pytest.fixture
def mock_supabase():
    """Mock Supabase client that returns fixture data."""
    mock_client = MagicMock()
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.neq.return_value = mock_table
    mock_table.gte.return_value = mock_table
    mock_table.in_.return_value = mock_table
    mock_table.not_.return_value = mock_table
    mock_table.is_.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.insert.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.upsert.return_value = mock_table
    mock_table.delete.return_value = mock_table

    execute_result = MagicMock()
    execute_result.data = []
    execute_result.count = 0
    mock_table.execute.return_value = execute_result

    mock_client.table.return_value = mock_table
    return mock_client


@pytest.fixture
def sample_job_raw():
    """A sample jobs_raw record."""
    return {
        "job_hash": "abc123def456",
        "title": "Senior Software Engineer",
        "company": "TechCorp",
        "description": "We are looking for a senior engineer with Python, AWS, and React experience. "
                       "You will build scalable systems and mentor junior developers.",
        "location": "Dublin, Ireland",
        "apply_url": "https://techcorp.com/jobs/123",
        "source": "linkedin",
        "experience_level": "senior",
        "job_type": "full_time",
        "query_hash": "q1hash",
        "scraped_at": "2026-03-31T07:00:00",
    }


@pytest.fixture
def sample_resume_tex():
    """A sample LaTeX resume."""
    return r"""\documentclass[11pt]{article}
\begin{document}
\section*{John Doe}
Senior Software Engineer | Dublin, Ireland

\section*{Experience}
\textbf{Lead Engineer} — Acme Corp (2022--Present)
\begin{itemize}
\item Built microservices with Python, FastAPI, and AWS Lambda
\item Led team of 5 engineers, mentored 3 junior devs
\item Reduced API latency by 40\% through caching and async processing
\end{itemize}

\section*{Skills}
Python, TypeScript, React, AWS, PostgreSQL, Docker, Kubernetes
\end{document}"""
