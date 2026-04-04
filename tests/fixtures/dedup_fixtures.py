"""Synthetic test data for dedup and scoring tests."""

# 5 duplicate pairs: same job from different sources
DUPLICATE_PAIRS = [
    {
        "job_a": {"company": "Acme Inc", "title": "Backend Engineer", "description": "Build REST APIs using Python and FastAPI. 3+ years experience required.", "source": "linkedin"},
        "job_b": {"company": "Acme Inc.", "title": "Backend Engineer", "description": "Build REST APIs using Python and FastAPI. 3+ years experience required.", "source": "indeed"},
        "should_match": True,
    },
    {
        "job_a": {"company": "Google LLC", "title": "Software Engineer", "description": "Design and implement distributed systems at scale.", "source": "linkedin"},
        "job_b": {"company": "Google", "title": "Software Engineer", "description": "Design and implement distributed systems at scale.", "source": "adzuna"},
        "should_match": True,
    },
    {
        "job_a": {"company": "Stripe", "title": "Senior Backend Engineer", "description": "Build payment processing infrastructure.", "source": "hn"},
        "job_b": {"company": "Stripe", "title": "Sr Backend Engineer", "description": "Build payment processing infrastructure.", "source": "yc"},
        "should_match": True,  # Fuzzy tier catches "Senior" vs "Sr"
    },
    {
        "job_a": {"company": "Meta Platforms", "title": "ML Engineer", "description": "Work on recommendation systems " + "x" * 500, "source": "linkedin"},
        "job_b": {"company": "Meta Platforms Inc", "title": "ML Engineer", "description": "Work on recommendation systems " + "x" * 500, "source": "indeed"},
        "should_match": True,
    },
    {
        "job_a": {"company": "Coinbase", "title": "Full Stack Developer", "description": "React frontend\n\nNode.js backend", "source": "linkedin"},
        "job_b": {"company": "Coinbase", "title": "Full Stack Developer", "description": "React frontend Node.js backend", "source": "indeed"},
        "should_match": True,
    },
]

# 3 near-miss pairs: similar but genuinely different jobs
NEAR_MISS_PAIRS = [
    {
        "job_a": {"company": "Acme", "title": "Backend Engineer", "description": "Build REST APIs using Python and FastAPI."},
        "job_b": {"company": "Acme", "title": "Frontend Engineer", "description": "Build React applications with TypeScript."},
        "should_match": False,
    },
    {
        "job_a": {"company": "Google", "title": "Software Engineer", "description": "Work on Google Search ranking algorithms."},
        "job_b": {"company": "Google", "title": "Software Engineer", "description": "Work on YouTube content recommendation systems."},
        "should_match": False,
    },
    {
        "job_a": {"company": "Stripe", "title": "Backend Engineer", "description": "Build payment APIs for global markets."},
        "job_b": {"company": "Square", "title": "Backend Engineer", "description": "Build payment APIs for small businesses."},
        "should_match": False,
    },
]

# Edge cases
EDGE_CASES = {
    "empty_description": {"company": "Acme", "title": "Engineer", "description": ""},
    "short_description": {"company": "Acme", "title": "Engineer", "description": "Short."},
    "long_description": {"company": "Acme", "title": "Engineer", "description": "x" * 5000},
    "missing_company": {"company": "", "title": "Engineer", "description": "Full description here." * 10},
    "unicode": {"company": "Unicod\u00e9 Ltd", "title": "Eng\u00efn\u00eb\u00ebr", "description": "W\u00f6rk with d\u00e4t\u00e4"},
}
