from unittest.mock import patch

from retrieval import dedup

# NOTE: description deliberately >= dedup.MIN_DESCRIPTION_CHARS (200). The
# task-14 brief's own fixture used a ~30-char description, which is below
# that guard and would make find_semantic_duplicate() short-circuit to None
# before ever calling embed()/similar_jobs_in_company() -- silently passing
# test_returns_none_below_threshold and test_never_matches_a_job_against_itself
# for the wrong reason, and failing test_returns_match_above_threshold
# outright (None["job_hash"]). Lengthened so all three actually exercise the
# matching logic they claim to test; test_skips_jobs_with_no_description is
# unaffected since it overrides description to "".
JOB = {
    "job_hash": "new",
    "company": "TREQS",
    "title": "Backend Software Engineer",
    "description": (
        "Build Python services on AWS. We design, build, and operate "
        "scalable backend microservices on AWS using Python, Docker, and "
        "Kubernetes, working closely with the platform team to ship "
        "production-grade APIs and CI/CD pipelines for our engineering org."
    ),
}


def test_returns_match_above_threshold():
    with patch.object(dedup, "embed", return_value=[0.1] * 768), \
         patch.object(dedup, "similar_jobs_in_company",
                      return_value=[{"job_hash": "old", "similarity": 0.96}]):
        assert dedup.find_semantic_duplicate(JOB)["job_hash"] == "old"


def test_returns_none_below_threshold():
    with patch.object(dedup, "embed", return_value=[0.1] * 768), \
         patch.object(dedup, "similar_jobs_in_company", return_value=[]):
        assert dedup.find_semantic_duplicate(JOB) is None


def test_never_matches_a_job_against_itself():
    with patch.object(dedup, "embed", return_value=[0.1] * 768), \
         patch.object(dedup, "similar_jobs_in_company",
                      return_value=[{"job_hash": "new", "similarity": 1.0}]):
        assert dedup.find_semantic_duplicate(JOB) is None


def test_skips_jobs_with_no_description():
    # 18 IrishJobs listings have empty descriptions; embedding "" is noise
    # that would collapse unrelated roles together.
    with patch.object(dedup, "embed", side_effect=AssertionError("must not embed")):
        assert dedup.find_semantic_duplicate({**JOB, "description": ""}) is None


def test_threshold_is_pinned():
    assert dedup.SEMANTIC_THRESHOLD == 0.93
