"""Unit tests for JD keyword extraction utility."""
from utils.keyword_extractor import extract_keywords


def test_extracts_tech_skills():
    jd = "We need a developer with Python, Kubernetes, and GraphQL experience."
    keywords = extract_keywords(jd)
    assert "python" in keywords
    assert "kubernetes" in keywords
    assert "graphql" in keywords


def test_returns_top_10():
    jd = "Python Java Go Rust C++ TypeScript React Angular Vue Svelte Kubernetes Docker Terraform AWS"
    keywords = extract_keywords(jd, max_keywords=10)
    assert len(keywords) <= 10


def test_deduplicates():
    jd = "Python python PYTHON PyThOn experience with python"
    keywords = extract_keywords(jd)
    assert keywords.count("python") == 1


def test_ignores_common_words():
    jd = "We are looking for a great engineer with good communication skills"
    keywords = extract_keywords(jd)
    assert "we" not in keywords
    assert "are" not in keywords
    assert "looking" not in keywords


def test_handles_empty():
    assert extract_keywords("") == []
    assert extract_keywords(None) == []


def test_extracts_multi_word_terms():
    jd = "Experience with machine learning, CI/CD pipelines, and Next.js required."
    keywords = extract_keywords(jd)
    assert "machine learning" in keywords
    assert "ci/cd" in keywords
    assert "next.js" in keywords


def test_frequency_ordering():
    """More frequent keywords should appear earlier."""
    jd = "Docker Docker Docker Python Python Terraform"
    keywords = extract_keywords(jd)
    docker_idx = keywords.index("docker")
    python_idx = keywords.index("python")
    terraform_idx = keywords.index("terraform")
    assert docker_idx < python_idx < terraform_idx


def test_fills_with_non_stop_words():
    """When fewer than max_keywords tech terms, fill with frequent non-stop words."""
    jd = "Python experience. Strong communication and leadership required. Analytical mindset."
    keywords = extract_keywords(jd, max_keywords=5)
    assert "python" in keywords
    assert len(keywords) >= 2  # should have at least python + some non-stop words


def test_max_keywords_zero():
    assert extract_keywords("Python Java Go", max_keywords=0) == []


def test_preserves_dotted_terms():
    jd = "Proficiency in Node.js, Vue.js, and .NET is a must."
    keywords = extract_keywords(jd)
    assert "node.js" in keywords
    assert "vue.js" in keywords
    assert ".net" in keywords


def test_slash_terms():
    jd = "Experience with CI/CD and ETL/ELT processes."
    keywords = extract_keywords(jd)
    assert "ci/cd" in keywords
