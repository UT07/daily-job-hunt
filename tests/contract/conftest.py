"""Contract test fixtures — validates state machine I/O shapes."""
import pytest


@pytest.fixture
def load_config_output():
    return {
        "user_id": "test-user-1",
        "queries": ["software engineer", "python developer"],
        "locations": ["ireland"],
        "min_match_score": 60,
        "query_hash": "a1b2c3d4e5f6",
    }

@pytest.fixture
def scraper_output():
    return {"count": 5, "source": "linkedin", "apify_cost_cents": 2}

@pytest.fixture
def scraper_error_output():
    return {"count": 0, "source": "linkedin", "error": "actor_timeout"}

@pytest.fixture
def dedup_output():
    return {"new_job_hashes": ["hash1", "hash2", "hash3"], "total_new": 3}

@pytest.fixture
def score_output():
    return {
        "matched_items": [
            {"job_hash": "hash1", "user_id": "test-user-1", "light_touch": True},
            {"job_hash": "hash2", "user_id": "test-user-1", "light_touch": False},
        ],
        "matched_count": 2,
    }

@pytest.fixture
def tailor_output():
    return {"job_hash": "hash1", "tex_s3_key": "users/u1/resumes/hash1_tailored.tex", "user_id": "test-user-1"}

@pytest.fixture
def compile_output():
    return {"job_hash": "hash1", "pdf_s3_key": "users/u1/resumes/hash1_tailored.pdf", "user_id": "test-user-1", "doc_type": "resume"}

@pytest.fixture
def compile_failure_output():
    return {"job_hash": "hash1", "pdf_s3_key": None, "tex_s3_key": "users/u1/resumes/hash1.tex", "user_id": "test-user-1", "doc_type": "resume", "error": "tectonic_not_available"}
