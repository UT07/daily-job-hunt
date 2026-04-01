"""AI Scoring Quality Tests -- C1.

Validates that the 3-perspective scoring pipeline produces consistent,
sensible results for known job+resume pairs. Uses deterministic mocked AI
responses to verify the scoring math, field extraction, and validation logic.

Run: python -m pytest tests/quality/ -v
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from scrapers.base import Job
from resume_scorer import score_resume, _validate_scores, score_and_improve

# ---------------------------------------------------------------------------
# Golden test data: 10 job+resume pairs with expected score ranges
# ---------------------------------------------------------------------------

GOLDEN_PAIRS = [
    {
        "id": "perfect_match",
        "job": {
            "title": "Senior Python Engineer",
            "company": "TechCorp",
            "location": "Dublin, Ireland",
            "description": "Looking for a Python expert with AWS, FastAPI, and PostgreSQL. "
                           "Must have 3+ years experience building microservices.",
            "source": "linkedin",
        },
        "resume_tex": r"""\documentclass[11pt]{article}
\begin{document}
\section*{Jane Doe — Senior Python Engineer}
\section*{Experience}
\textbf{Backend Engineer} — CloudCo (2021--Present)
\begin{itemize}
\item Built FastAPI microservices on AWS Lambda processing 10k req/s
\item Managed PostgreSQL databases with 99.9\% uptime
\item Led migration from monolith to microservices, reducing deploy time by 60\%
\end{itemize}
\section*{Skills}
Python, AWS, FastAPI, PostgreSQL, Docker, Kubernetes
\end{document}""",
        "ai_response": {
            "ats_score": 92,
            "ats_feedback": "Strong keyword match for Python, AWS, FastAPI, PostgreSQL",
            "hiring_manager_score": 90,
            "hm_feedback": "Excellent impact metrics, relevant microservices experience",
            "tech_recruiter_score": 91,
            "tr_feedback": "All required skills demonstrated with project evidence",
            "improvements": [],
            "fabrication_detected": False,
        },
        "expected_range": {"ats": (85, 100), "hm": (85, 100), "tr": (85, 100)},
    },
    {
        "id": "partial_match_missing_key_skill",
        "job": {
            "title": "Rust Systems Engineer",
            "company": "LowLatency Inc",
            "location": "Remote",
            "description": "Seeking a Rust expert with experience in async runtimes (tokio), "
                           "zero-copy networking, and kernel-level optimization.",
            "source": "hn_hiring",
        },
        "resume_tex": r"""\documentclass[11pt]{article}
\begin{document}
\section*{Jane Doe — Software Engineer}
\section*{Experience}
\textbf{Backend Engineer} — CloudCo (2021--Present)
\begin{itemize}
\item Built Python microservices on AWS Lambda
\item Some C++ experience for performance-critical paths
\end{itemize}
\section*{Skills}
Python, C++, AWS, Docker
\end{document}""",
        "ai_response": {
            "ats_score": 35,
            "ats_feedback": "No Rust, tokio, or systems programming keywords",
            "hiring_manager_score": 30,
            "hm_feedback": "No relevant systems engineering experience",
            "tech_recruiter_score": 25,
            "tr_feedback": "Missing all required skills: Rust, tokio, zero-copy networking",
            "improvements": ["Add Rust projects if candidate has any", "Emphasize C++ low-level experience"],
            "fabrication_detected": False,
        },
        "expected_range": {"ats": (0, 50), "hm": (0, 50), "tr": (0, 50)},
    },
    {
        "id": "junior_role_with_grad",
        "job": {
            "title": "Junior DevOps Engineer",
            "company": "StartupXYZ",
            "location": "Dublin, Ireland",
            "description": "Entry-level DevOps role. Experience with CI/CD, Docker, and Linux. "
                           "We value learning ability over years of experience.",
            "source": "glassdoor",
        },
        "resume_tex": r"""\documentclass[11pt]{article}
\begin{document}
\section*{Fresh Graduate — MSc Cloud Computing}
\section*{Education}
MSc Cloud Computing, NCI Dublin (2024)
\section*{Projects}
\begin{itemize}
\item CI/CD pipeline with GitHub Actions and Docker for MSc thesis project
\item Automated deployment to AWS using Terraform in coursework
\end{itemize}
\section*{Skills}
Docker, Linux, GitHub Actions, Terraform, Python, AWS
\end{document}""",
        "ai_response": {
            "ats_score": 82,
            "ats_feedback": "Good keyword match. Missing production experience.",
            "hiring_manager_score": 85,
            "hm_feedback": "Shows strong learning ability, relevant projects",
            "tech_recruiter_score": 78,
            "tr_feedback": "Has Docker and CI/CD. Limited production Linux experience.",
            "improvements": ["Expand Docker project details", "Add Linux administration coursework"],
            "fabrication_detected": False,
        },
        "expected_range": {"ats": (70, 90), "hm": (75, 95), "tr": (65, 85)},
    },
    {
        "id": "overqualified_for_internship",
        "job": {
            "title": "Software Engineering Intern",
            "company": "BigBank",
            "location": "London, UK",
            "description": "Summer internship for current students. Basic Java knowledge required.",
            "source": "indeed",
        },
        "resume_tex": r"""\documentclass[11pt]{article}
\begin{document}
\section*{Jane Doe — Staff Engineer (12 YoE)}
\section*{Experience}
\textbf{Staff Engineer} — FAANG (2018--Present)
\begin{itemize}
\item Architected distributed systems serving 100M users
\item Led team of 15 engineers across 3 timezones
\end{itemize}
\section*{Skills}
Java, Python, Kotlin, Go, AWS, GCP, distributed systems
\end{document}""",
        "ai_response": {
            "ats_score": 70,
            "ats_feedback": "Has Java, but clearly overqualified for internship",
            "hiring_manager_score": 40,
            "hm_feedback": "Overqualified. Internship is for students, not staff engineers.",
            "tech_recruiter_score": 55,
            "tr_feedback": "Skills far exceed requirements. Red flag: flight risk.",
            "improvements": [],
            "fabrication_detected": False,
        },
        "expected_range": {"ats": (50, 80), "hm": (20, 60), "tr": (30, 70)},
    },
    {
        "id": "remote_india_role_good_fit",
        "job": {
            "title": "Full Stack Developer",
            "company": "RemoteFirst.io",
            "location": "Remote, India",
            "description": "Remote full-stack role. React + Node.js + PostgreSQL. "
                           "Salary range: 15-25 LPA. Must be based in India.",
            "source": "linkedin",
        },
        "resume_tex": r"""\documentclass[11pt]{article}
\begin{document}
\section*{Full Stack Developer — Dublin, Ireland}
\section*{Experience}
\begin{itemize}
\item Built React + Node.js e-commerce platform
\item PostgreSQL database design and optimization
\end{itemize}
\section*{Skills}
React, Node.js, PostgreSQL, TypeScript, Docker
\end{document}""",
        "ai_response": {
            "ats_score": 88,
            "ats_feedback": "Excellent keyword coverage for React, Node.js, PostgreSQL",
            "hiring_manager_score": 75,
            "hm_feedback": "Good fit technically. Geographic concern: candidate in Ireland, role is India-based.",
            "tech_recruiter_score": 86,
            "tr_feedback": "All required skills covered. Remote-from-Ireland may work.",
            "improvements": ["Clarify remote work availability from Ireland"],
            "fabrication_detected": False,
        },
        "expected_range": {"ats": (80, 95), "hm": (60, 85), "tr": (75, 95)},
    },
    {
        "id": "data_science_vs_web_dev",
        "job": {
            "title": "Machine Learning Engineer",
            "company": "AI Labs",
            "location": "San Francisco, CA",
            "description": "Building production ML pipelines. PyTorch, MLOps, feature stores, "
                           "model serving at scale. PhD preferred.",
            "source": "hn_hiring",
        },
        "resume_tex": r"""\documentclass[11pt]{article}
\begin{document}
\section*{Web Developer}
\section*{Experience}
\begin{itemize}
\item Built React frontends and REST APIs
\item Basic Python scripting for data cleaning
\end{itemize}
\section*{Skills}
React, JavaScript, HTML/CSS, Python basics
\end{document}""",
        "ai_response": {
            "ats_score": 20,
            "ats_feedback": "No ML, PyTorch, or MLOps keywords",
            "hiring_manager_score": 15,
            "hm_feedback": "No ML experience whatsoever. Fundamental mismatch.",
            "tech_recruiter_score": 10,
            "tr_feedback": "0% required skills coverage. No ML, no PhD, no model serving.",
            "improvements": [],
            "fabrication_detected": False,
        },
        "expected_range": {"ats": (0, 35), "hm": (0, 30), "tr": (0, 25)},
    },
    {
        "id": "fabrication_detected",
        "job": {
            "title": "Cloud Architect",
            "company": "CloudNative Co",
            "location": "Dublin",
            "description": "AWS Solutions Architect with 5+ years, GCP multi-cloud experience.",
            "source": "adzuna",
        },
        "resume_tex": r"""\documentclass[11pt]{article}
\begin{document}
\section*{Cloud Architect — 10 years at Google}
\section*{Experience}
\textbf{Principal Architect} — Google Cloud (2015--Present)
\begin{itemize}
\item Designed GCP for Fortune 500 companies
\item AWS SA Professional certified
\end{itemize}
\end{document}""",
        "ai_response": {
            "ats_score": 60,
            "ats_feedback": "Keywords present but resume appears fabricated",
            "hiring_manager_score": 55,
            "hm_feedback": "Suspicious: claims 10 years at Google as principal architect",
            "tech_recruiter_score": 50,
            "tr_feedback": "Cannot verify claims",
            "improvements": [],
            "fabrication_detected": True,
        },
        "expected_range": {"ats": (40, 75), "hm": (35, 70), "tr": (30, 65)},
    },
    {
        "id": "contract_role_with_relevant_experience",
        "job": {
            "title": "Contract DevOps Engineer (6 months)",
            "company": "FinServ Ltd",
            "location": "Dublin, Ireland",
            "description": "6-month contract. Kubernetes, Terraform, CI/CD pipelines, "
                           "monitoring with Prometheus/Grafana. IR35 outside.",
            "source": "linkedin",
        },
        "resume_tex": r"""\documentclass[11pt]{article}
\begin{document}
\section*{DevOps Engineer}
\section*{Experience}
\textbf{SRE} — TechStartup (2022--Present)
\begin{itemize}
\item Managed Kubernetes clusters (EKS) with 50+ microservices
\item Terraform IaC for all AWS infrastructure
\item Built CI/CD with GitHub Actions, ArgoCD
\item Prometheus + Grafana monitoring stack
\end{itemize}
\section*{Skills}
Kubernetes, Terraform, AWS, CI/CD, Prometheus, Grafana, Docker
\end{document}""",
        "ai_response": {
            "ats_score": 95,
            "ats_feedback": "All keywords matched: K8s, Terraform, CI/CD, Prometheus, Grafana",
            "hiring_manager_score": 88,
            "hm_feedback": "Direct experience with all required tools, relevant impact",
            "tech_recruiter_score": 93,
            "tr_feedback": "100% required skills coverage. Strong candidate.",
            "improvements": [],
            "fabrication_detected": False,
        },
        "expected_range": {"ats": (85, 100), "hm": (80, 100), "tr": (85, 100)},
    },
    {
        "id": "career_changer_bootcamp_grad",
        "job": {
            "title": "Backend Developer",
            "company": "SaaS Corp",
            "location": "Remote, EU",
            "description": "Python/Django backend. 2+ years experience. REST API design, "
                           "testing, PostgreSQL, Redis.",
            "source": "glassdoor",
        },
        "resume_tex": r"""\documentclass[11pt]{article}
\begin{document}
\section*{Career Changer — Former Accountant}
\section*{Education}
Coding Bootcamp, 2024 (12 weeks, full-stack)
\section*{Projects}
\begin{itemize}
\item Built Django REST API for personal project (CRUD app)
\item Basic PostgreSQL queries in bootcamp projects
\end{itemize}
\section*{Skills}
Python, Django (beginner), PostgreSQL, HTML/CSS
\end{document}""",
        "ai_response": {
            "ats_score": 55,
            "ats_feedback": "Has Django and PostgreSQL keywords but beginner level",
            "hiring_manager_score": 45,
            "hm_feedback": "Insufficient experience. Role needs 2+ years, candidate has 12-week bootcamp.",
            "tech_recruiter_score": 40,
            "tr_feedback": "Missing Redis, testing, and production experience. Beginner Django.",
            "improvements": ["Expand Django project to show API design skills", "Add testing section"],
            "fabrication_detected": False,
        },
        "expected_range": {"ats": (40, 70), "hm": (30, 60), "tr": (25, 55)},
    },
    {
        "id": "perfect_sre_match_all_pass",
        "job": {
            "title": "Site Reliability Engineer",
            "company": "ScaleUp",
            "location": "Dublin, Ireland",
            "description": "SRE role: incident response, SLOs/SLIs, Kubernetes, "
                           "Python/Go automation, on-call rotation. 3+ years.",
            "source": "linkedin",
        },
        "resume_tex": r"""\documentclass[11pt]{article}
\begin{document}
\section*{SRE — 4 Years Experience}
\section*{Experience}
\textbf{SRE} — PlatformCo (2021--Present)
\begin{itemize}
\item Defined and tracked SLOs for 20+ services (99.95\% target)
\item Led incident response for P0/P1 incidents, MTTR reduced by 40\%
\item Kubernetes cluster management (GKE, 200+ pods)
\item Python automation for toil reduction (saved 10 hrs/week)
\end{itemize}
\section*{Skills}
SRE, Kubernetes, Python, Go, Prometheus, PagerDuty, Terraform
\end{document}""",
        "ai_response": {
            "ats_score": 96,
            "ats_feedback": "Pass",
            "hiring_manager_score": 94,
            "hm_feedback": "Pass",
            "tech_recruiter_score": 95,
            "tr_feedback": "Pass",
            "improvements": [],
            "fabrication_detected": False,
        },
        "expected_range": {"ats": (90, 100), "hm": (88, 100), "tr": (90, 100)},
    },
]


def _make_job(data: dict) -> Job:
    """Create a Job from golden pair job data."""
    return Job(
        title=data["title"],
        company=data["company"],
        location=data["location"],
        description=data["description"],
        apply_url=f"https://example.com/jobs/{data['company'].lower().replace(' ', '-')}",
        source=data["source"],
    )


def _make_ai_client(ai_response: dict) -> MagicMock:
    """Create a mock AIClient that returns a deterministic JSON response."""
    client = MagicMock()
    client.complete_with_info.return_value = {
        "response": json.dumps(ai_response),
        "provider": "test",
        "model": "test-model",
    }
    return client


# ---------------------------------------------------------------------------
# C1.1: Scoring returns results within expected ranges
# ---------------------------------------------------------------------------

class TestScoringRanges:
    """Verify that mocked AI responses produce scores in expected ranges."""

    @pytest.mark.quality
    @pytest.mark.parametrize("pair", GOLDEN_PAIRS, ids=[p["id"] for p in GOLDEN_PAIRS])
    def test_score_within_expected_range(self, pair):
        """Each golden pair should produce scores within the defined range."""
        job = _make_job(pair["job"])
        client = _make_ai_client(pair["ai_response"])

        with patch("resume_scorer.log_quality"):
            scores = score_resume(pair["resume_tex"], job, client)

        expected = pair["expected_range"]
        ats_lo, ats_hi = expected["ats"]
        hm_lo, hm_hi = expected["hm"]
        tr_lo, tr_hi = expected["tr"]

        assert ats_lo <= scores["ats_score"] <= ats_hi, (
            f"ATS score {scores['ats_score']} outside [{ats_lo}, {ats_hi}]"
        )
        assert hm_lo <= scores["hiring_manager_score"] <= hm_hi, (
            f"HM score {scores['hiring_manager_score']} outside [{hm_lo}, {hm_hi}]"
        )
        assert tr_lo <= scores["tech_recruiter_score"] <= tr_hi, (
            f"TR score {scores['tech_recruiter_score']} outside [{tr_lo}, {tr_hi}]"
        )


# ---------------------------------------------------------------------------
# C1.2: Three-perspective scores are computed and structured correctly
# ---------------------------------------------------------------------------

class TestScoreStructure:
    """Verify that score_resume returns all required fields."""

    @pytest.mark.quality
    def test_all_score_keys_present(self):
        """score_resume must return all 3 scores, feedbacks, improvements, and fabrication flag."""
        pair = GOLDEN_PAIRS[0]
        job = _make_job(pair["job"])
        client = _make_ai_client(pair["ai_response"])

        with patch("resume_scorer.log_quality"):
            scores = score_resume(pair["resume_tex"], job, client)

        required_keys = {
            "ats_score", "ats_feedback",
            "hiring_manager_score", "hm_feedback",
            "tech_recruiter_score", "tr_feedback",
            "improvements", "fabrication_detected",
        }
        assert required_keys.issubset(scores.keys()), (
            f"Missing keys: {required_keys - scores.keys()}"
        )

    @pytest.mark.quality
    def test_scores_are_integers_in_range(self):
        """All score values must be integers between 0 and 100."""
        pair = GOLDEN_PAIRS[0]
        job = _make_job(pair["job"])
        client = _make_ai_client(pair["ai_response"])

        with patch("resume_scorer.log_quality"):
            scores = score_resume(pair["resume_tex"], job, client)

        for key in ("ats_score", "hiring_manager_score", "tech_recruiter_score"):
            val = scores[key]
            assert isinstance(val, int), f"{key} should be int, got {type(val)}"
            assert 0 <= val <= 100, f"{key}={val} out of [0, 100]"

    @pytest.mark.quality
    def test_feedbacks_are_strings(self):
        """Feedback fields must be strings."""
        pair = GOLDEN_PAIRS[0]
        job = _make_job(pair["job"])
        client = _make_ai_client(pair["ai_response"])

        with patch("resume_scorer.log_quality"):
            scores = score_resume(pair["resume_tex"], job, client)

        for key in ("ats_feedback", "hm_feedback", "tr_feedback"):
            assert isinstance(scores[key], str), f"{key} should be str"

    @pytest.mark.quality
    def test_improvements_is_list_of_strings(self):
        """Improvements must be a list of strings."""
        pair = GOLDEN_PAIRS[1]  # partial match, has improvements
        job = _make_job(pair["job"])
        client = _make_ai_client(pair["ai_response"])

        with patch("resume_scorer.log_quality"):
            scores = score_resume(pair["resume_tex"], job, client)

        assert isinstance(scores["improvements"], list)
        for item in scores["improvements"]:
            assert isinstance(item, str)

    @pytest.mark.quality
    def test_fabrication_flag_is_boolean(self):
        """fabrication_detected must be a boolean."""
        pair = GOLDEN_PAIRS[6]  # fabrication_detected = True
        job = _make_job(pair["job"])
        client = _make_ai_client(pair["ai_response"])

        with patch("resume_scorer.log_quality"):
            scores = score_resume(pair["resume_tex"], job, client)

        assert isinstance(scores["fabrication_detected"], bool)
        assert scores["fabrication_detected"] is True


# ---------------------------------------------------------------------------
# C1.3: _validate_scores edge cases
# ---------------------------------------------------------------------------

class TestValidateScores:
    """Direct tests for _validate_scores — the score sanitizer."""

    @pytest.mark.quality
    def test_missing_keys_default_to_zero(self):
        """Missing score keys should default to 0."""
        raw = {}
        validated = _validate_scores(raw, company="TestCo")
        assert validated["ats_score"] == 0
        assert validated["hiring_manager_score"] == 0
        assert validated["tech_recruiter_score"] == 0

    @pytest.mark.quality
    def test_scores_clamped_to_0_100(self):
        """Scores above 100 or below 0 are clamped."""
        raw = {"ats_score": 150, "hiring_manager_score": -20, "tech_recruiter_score": 50}
        validated = _validate_scores(raw, company="TestCo")
        assert validated["ats_score"] == 100
        assert validated["hiring_manager_score"] == 0
        assert validated["tech_recruiter_score"] == 50

    @pytest.mark.quality
    def test_non_numeric_scores_default_to_zero(self):
        """Non-numeric score values should default to 0."""
        raw = {"ats_score": "high", "hiring_manager_score": None, "tech_recruiter_score": ""}
        validated = _validate_scores(raw, company="TestCo")
        assert validated["ats_score"] == 0
        assert validated["hiring_manager_score"] == 0
        assert validated["tech_recruiter_score"] == 0

    @pytest.mark.quality
    def test_float_scores_are_rounded(self):
        """Float scores should be rounded to nearest integer."""
        raw = {"ats_score": 85.7, "hiring_manager_score": 79.2, "tech_recruiter_score": 90.5}
        validated = _validate_scores(raw, company="TestCo")
        assert validated["ats_score"] == 86
        assert validated["hiring_manager_score"] == 79
        assert validated["tech_recruiter_score"] == 90  # Python rounds 90.5 to 90

    @pytest.mark.quality
    def test_improvements_non_list_defaults_empty(self):
        """Non-list improvements should become empty list."""
        raw = {"improvements": "just a string"}
        validated = _validate_scores(raw, company="TestCo")
        assert validated["improvements"] == []

    @pytest.mark.quality
    def test_fabrication_flag_absent_defaults_false(self):
        """Missing fabrication_detected should default to False."""
        raw = {"ats_score": 80}
        validated = _validate_scores(raw, company="TestCo")
        assert validated["fabrication_detected"] is False


# ---------------------------------------------------------------------------
# C1.4: score_and_improve integration (mocked)
# ---------------------------------------------------------------------------

class TestScoreAndImprove:
    """Test the score-and-improve loop with deterministic responses."""

    @pytest.mark.quality
    def test_all_pass_on_first_round(self):
        """If all scores >= 85 on first try, no improvement rounds run."""
        pair = GOLDEN_PAIRS[0]  # perfect match
        job = _make_job(pair["job"])
        client = _make_ai_client(pair["ai_response"])

        with patch("resume_scorer.log_quality"):
            final_tex, final_scores = score_and_improve(
                pair["resume_tex"], job, client, min_score=85, max_rounds=3
            )

        # Only 1 call to complete_with_info (scoring), no improvement call
        assert client.complete_with_info.call_count == 1
        assert final_scores["ats_score"] >= 85
        assert final_scores["hiring_manager_score"] >= 85
        assert final_scores["tech_recruiter_score"] >= 85

    @pytest.mark.quality
    def test_fabrication_stops_improvement_loop(self):
        """If fabrication is detected, the loop stops immediately."""
        pair = GOLDEN_PAIRS[6]  # fabrication_detected = True
        job = _make_job(pair["job"])
        client = _make_ai_client(pair["ai_response"])

        with patch("resume_scorer.log_quality"):
            final_tex, final_scores = score_and_improve(
                pair["resume_tex"], job, client, min_score=85, max_rounds=3
            )

        # Only 1 scoring call, no improvement attempts
        assert client.complete_with_info.call_count == 1
        assert final_scores["fabrication_detected"] is True

    @pytest.mark.quality
    def test_error_returns_zero_scores(self):
        """If AI fails, all scores default to 0."""
        job = _make_job(GOLDEN_PAIRS[0]["job"])
        client = MagicMock()
        client.complete_with_info.side_effect = RuntimeError("AI provider down")

        with patch("resume_scorer.log_quality"):
            final_tex, final_scores = score_and_improve(
                GOLDEN_PAIRS[0]["resume_tex"], job, client, min_score=85, max_rounds=1
            )

        assert final_scores["ats_score"] == 0
        assert final_scores["hiring_manager_score"] == 0
        assert final_scores["tech_recruiter_score"] == 0
