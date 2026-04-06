"""JD keyword extraction utility.

Extracts tech keywords from job descriptions for use by tailorer.py
(resume prompt v2) and cover_letter.py (keyword analysis).

No external dependencies — pure Python with regex-based extraction.
"""
import re
from collections import Counter
from typing import Optional

# ── Multi-word tech terms (checked first, case-insensitive) ─────────────
MULTI_WORD_TERMS: set[str] = {
    "machine learning",
    "deep learning",
    "natural language processing",
    "computer vision",
    "data engineering",
    "data science",
    "data analysis",
    "software engineering",
    "site reliability",
    "test driven development",
    "continuous integration",
    "continuous deployment",
    "event driven",
    "message queue",
    "load balancing",
    "infrastructure as code",
    "design patterns",
    "system design",
    "distributed systems",
    "object oriented",
    "functional programming",
    "version control",
    "unit testing",
    "api design",
    "rest api",
    "web services",
    "micro services",
    "microservices",
}

# ── Single-word + dotted/slashed tech terms ─────────────────────────────
TECH_KEYWORDS: set[str] = {
    # Languages
    "python", "java", "javascript", "typescript", "go", "golang", "rust",
    "c++", "c#", "ruby", "scala", "kotlin", "swift", "php", "perl",
    "r", "sql", "nosql", "bash", "shell", "lua", "elixir", "haskell",
    "clojure", "dart",
    # Frontend frameworks
    "react", "angular", "vue", "svelte", "next.js", "nuxt.js", "vue.js",
    "gatsby", "remix", "tailwind", "bootstrap", "webpack", "vite",
    # Backend frameworks
    "node.js", "express", "fastapi", "django", "flask", "spring",
    "rails", "laravel", "nestjs", "gin", "fiber", "actix",
    # Cloud & infra
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform", "ansible",
    "jenkins", "gitlab", "github", "circleci", "helm", "pulumi",
    "cloudformation", "serverless", "lambda", "fargate", "ecs", "eks",
    "s3", "ec2", "rds", "dynamodb", "sqs", "sns", "cloudwatch",
    # Databases
    "postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch",
    "cassandra", "sqlite", "supabase", "firebase", "cockroachdb",
    "neo4j", "influxdb", "bigquery",
    # Data / ML
    "spark", "kafka", "airflow", "pandas", "numpy", "tensorflow",
    "pytorch", "scikit-learn", "mlflow", "databricks", "snowflake",
    "dbt", "tableau", "powerbi", "grafana", "prometheus",
    # DevOps / tools
    "ci/cd", "git", "linux", "nginx", "apache", "rabbitmq",
    "celery", "graphql", "grpc", "rest", "oauth", "jwt",
    "openapi", "swagger",
    # Concepts / methodologies
    "agile", "scrum", "kanban", "devops", "mlops", "sre",
    "tdd", "bdd", "oop",
    # .NET ecosystem
    ".net", "asp.net", "blazor", "xamarin", "maui",
    # Mobile
    "ios", "android", "flutter", "react native",
    # Testing
    "jest", "pytest", "cypress", "playwright", "selenium",
    "mocha", "chai", "junit", "testng",
    # Security
    "oauth2", "saml", "sso", "encryption", "tls", "ssl",
    # Misc
    "etl", "etl/elt",
}

# ── Stop words to ignore ────────────────────────────────────────────────
STOP_WORDS: set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "also",
    "am", "an", "and", "any", "are", "aren't", "as", "at", "be",
    "because", "been", "before", "being", "below", "between", "both",
    "but", "by", "can", "could", "did", "do", "does", "doing", "don't",
    "down", "during", "each", "few", "for", "from", "further", "get",
    "got", "great", "good", "had", "has", "have", "having", "he", "her",
    "here", "hers", "herself", "him", "himself", "his", "how", "i",
    "if", "in", "into", "is", "isn't", "it", "its", "itself", "just",
    "let", "like", "ll", "looking", "make", "me", "might", "more",
    "most", "must", "my", "myself", "need", "no", "nor", "not", "now",
    "of", "off", "on", "once", "only", "or", "other", "our", "ours",
    "ourselves", "out", "over", "own", "part", "re", "role", "s",
    "same", "she", "should", "so", "some", "such", "t", "than", "that",
    "the", "their", "theirs", "them", "themselves", "then", "there",
    "these", "they", "this", "those", "through", "to", "too", "under",
    "until", "up", "us", "ve", "very", "was", "we", "were", "what",
    "when", "where", "which", "while", "who", "whom", "why", "will",
    "with", "won't", "would", "you", "your", "yours", "yourself",
    "yourselves",
    # Job-posting filler words
    "ability", "apply", "candidate", "company", "environment",
    "experience", "including", "join", "minimum", "opportunity",
    "position", "preferred", "qualifications", "requirements",
    "required", "responsible", "seeking", "strong", "team", "work",
    "working", "years", "year",
}


def _find_multi_word_matches(text_lower: str) -> list[str]:
    """Find multi-word tech terms in the text. Returns matched terms."""
    found = []
    for term in MULTI_WORD_TERMS:
        # Use word boundary matching for multi-word terms
        pattern = re.escape(term)
        if re.search(rf"(?<![a-z]){pattern}(?![a-z])", text_lower):
            found.append(term)
    return found


def _find_special_terms(text: str) -> list[tuple[str, int]]:
    """Find dotted terms (node.js, .net), slashed terms (ci/cd, etl/elt),
    and C++ / C# style terms. Returns (term, count) pairs."""
    text_lower = text.lower()
    results: list[tuple[str, int]] = []

    # Dotted terms: .net, node.js, vue.js, next.js, asp.net, nuxt.js
    for term in TECH_KEYWORDS:
        if "." in term:
            escaped = re.escape(term)
            matches = re.findall(rf"(?<![a-z]){escaped}(?![a-z.])", text_lower)
            if matches:
                results.append((term, len(matches)))

    # Slashed terms: ci/cd, etl/elt
    for term in TECH_KEYWORDS:
        if "/" in term:
            escaped = re.escape(term)
            matches = re.findall(rf"(?<![a-z/]){escaped}(?![a-z/])", text_lower)
            if matches:
                results.append((term, len(matches)))

    # C++ and C#
    for term in ("c++", "c#"):
        if term in TECH_KEYWORDS:
            escaped = re.escape(term)
            matches = re.findall(rf"(?<![a-z]){escaped}(?![a-z+#])", text_lower)
            if matches:
                results.append((term, len(matches)))

    return results


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase words, preserving only alphanumeric chars."""
    return re.findall(r"[a-z0-9]+", text.lower())


def extract_keywords(
    jd: Optional[str],
    max_keywords: int = 10,
) -> list[str]:
    """Extract top keywords from a job description.

    Strategy:
    1. Find multi-word tech terms (e.g., "machine learning", "ci/cd")
    2. Find single-word tech terms by frequency
    3. Fill remaining slots with frequent non-stop words
    4. Return lowercase, deduplicated, ordered by frequency

    Args:
        jd: Job description text (or None/empty string).
        max_keywords: Maximum number of keywords to return.

    Returns:
        List of lowercase keywords, ordered by frequency (most frequent first).
    """
    if not jd or max_keywords <= 0:
        return []

    text_lower = jd.lower()
    seen: set[str] = set()
    keyword_counts: Counter[str] = Counter()

    # ── Pass 1: Multi-word tech terms ──────────────────────────────────
    multi_matches = _find_multi_word_matches(text_lower)
    for term in multi_matches:
        # Count occurrences
        count = len(re.findall(re.escape(term), text_lower))
        keyword_counts[term] = count
        seen.add(term)

    # ── Pass 1b: Special terms (dotted, slashed, C++/C#) ──────────────
    special_matches = _find_special_terms(jd)
    for term, count in special_matches:
        if term not in seen:
            keyword_counts[term] = count
            seen.add(term)

    # ── Pass 2: Single-word tech terms by frequency ────────────────────
    tokens = _tokenize(jd)
    token_counts = Counter(tokens)

    # Also check tokens against known single-word tech keywords
    single_tech = TECH_KEYWORDS - {t for t in TECH_KEYWORDS if "." in t or "/" in t or "+" in t or "#" in t}
    for token, count in token_counts.items():
        if token in single_tech and token not in seen:
            keyword_counts[token] = count
            seen.add(token)

    # ── Pass 3: Fill with frequent non-stop words ──────────────────────
    if len(keyword_counts) < max_keywords:
        for token, count in token_counts.most_common():
            if len(keyword_counts) >= max_keywords:
                break
            if (
                token not in seen
                and token not in STOP_WORDS
                and len(token) > 2  # skip very short non-tech tokens
            ):
                keyword_counts[token] = count
                seen.add(token)

    # ── Sort by frequency (descending), take top N ─────────────────────
    ranked = keyword_counts.most_common(max_keywords)
    return [term for term, _count in ranked]
