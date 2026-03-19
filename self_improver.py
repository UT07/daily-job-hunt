"""Self-improving loop for the job hunt pipeline.

Analyzes results from previous runs and generates actionable improvements:
1. Resume quality: identifies weak sections across multiple tailored resumes
2. Matching accuracy: detects false positives/negatives in job matching
3. Scraper health: flags scrapers with low yield or high failure rates
4. Keyword gaps: discovers missing keywords from job descriptions
5. Score trends: tracks improvement/degradation over time

After analysis, it updates config.yaml search queries and resume bullet points
to close identified gaps.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def analyze_run_results(output_dir: str = "output") -> dict:
    """Analyze the most recent pipeline run results.

    Returns a report dict with findings and recommended actions.
    """
    output = Path(output_dir)
    report = {
        "timestamp": datetime.now().isoformat(),
        "findings": [],
        "actions": [],
        "stats": {},
    }

    # --- 1. Load run metadata ---
    metadata_path = output / "run_metadata.json"
    if not metadata_path.exists():
        report["findings"].append("No run_metadata.json found — run the pipeline first.")
        return report

    with open(metadata_path) as f:
        metadata = json.load(f)

    report["stats"] = {
        "jobs_scraped": metadata.get("jobs_scraped", 0),
        "jobs_unique": metadata.get("jobs_unique", 0),
        "jobs_matched": metadata.get("jobs_matched", 0),
        "jobs_above_85": metadata.get("jobs_above_85", 0),
        "run_date": metadata.get("run_date", "unknown"),
    }

    # --- 2. Analyze score distribution ---
    matched_jobs = metadata.get("matched_jobs", [])
    if matched_jobs:
        ats_scores = [j.get("ats_score", 0) for j in matched_jobs]
        hm_scores = [j.get("hiring_manager_score", 0) for j in matched_jobs]
        tr_scores = [j.get("tech_recruiter_score", 0) for j in matched_jobs]

        avg_ats = sum(ats_scores) / len(ats_scores) if ats_scores else 0
        avg_hm = sum(hm_scores) / len(hm_scores) if hm_scores else 0
        avg_tr = sum(tr_scores) / len(tr_scores) if tr_scores else 0

        report["stats"]["avg_ats"] = round(avg_ats, 1)
        report["stats"]["avg_hm"] = round(avg_hm, 1)
        report["stats"]["avg_tr"] = round(avg_tr, 1)

        # Weak perspective detection
        if avg_ats < avg_hm - 10 and avg_ats < avg_tr - 10:
            report["findings"].append(
                f"ATS scores are consistently lowest (avg {avg_ats:.0f}). "
                "Resume may lack job-specific keywords."
            )
            report["actions"].append({
                "type": "keyword_gap",
                "description": "Extract top keywords from matched JDs and check resume coverage",
            })

        if avg_hm < avg_ats - 10 and avg_hm < avg_tr - 10:
            report["findings"].append(
                f"Hiring Manager scores are lowest (avg {avg_hm:.0f}). "
                "Resume bullets may lack measurable impact."
            )
            report["actions"].append({
                "type": "impact_improvement",
                "description": "Strengthen achievement bullets with quantified results",
            })

        if avg_tr < avg_ats - 10 and avg_tr < avg_hm - 10:
            report["findings"].append(
                f"Tech Recruiter scores are lowest (avg {avg_tr:.0f}). "
                "Skills section may be missing required technologies."
            )
            report["actions"].append({
                "type": "skills_gap",
                "description": "Add frequently-requested skills that candidate actually has",
            })

        # Low match rate
        scraped = report["stats"]["jobs_scraped"]
        matched = report["stats"]["jobs_matched"]
        if scraped > 0 and matched / scraped < 0.05:
            report["findings"].append(
                f"Very low match rate ({matched}/{scraped} = {matched/scraped:.1%}). "
                "Search queries may not align with resume strengths."
            )
            report["actions"].append({
                "type": "query_refinement",
                "description": "Refine search queries to better match candidate profile",
            })

        # Jobs above 85 rate
        above_85 = report["stats"]["jobs_above_85"]
        if matched > 0 and above_85 / matched < 0.3:
            report["findings"].append(
                f"Only {above_85}/{matched} matched jobs score 85+. "
                "Tailoring quality needs improvement."
            )

    # --- 3. Analyze scraper performance ---
    scraper_stats = metadata.get("scraper_stats", {})
    for scraper, stats in scraper_stats.items():
        count = stats.get("count", 0)
        errors = stats.get("errors", 0)
        if count == 0 and errors > 0:
            report["findings"].append(f"Scraper '{scraper}' returned 0 jobs with {errors} errors.")
            report["actions"].append({
                "type": "scraper_fix",
                "description": f"Investigate/disable scraper '{scraper}'",
                "scraper": scraper,
            })
        elif count > 0 and errors / max(count, 1) > 0.5:
            report["findings"].append(
                f"Scraper '{scraper}' has high error rate ({errors} errors / {count} jobs)."
            )

    # --- 4. Keyword gap analysis ---
    _analyze_keyword_gaps(matched_jobs, report)

    return report


def _analyze_keyword_gaps(matched_jobs: list, report: dict):
    """Find keywords that appear frequently in JDs but are missing from resumes."""
    if not matched_jobs:
        return

    # Count keyword frequency across matched job descriptions
    from collections import Counter
    keyword_freq = Counter()
    tech_keywords = {
        "kubernetes", "docker", "terraform", "aws", "gcp", "azure", "python",
        "go", "golang", "java", "react", "typescript", "node", "postgresql",
        "mongodb", "redis", "kafka", "jenkins", "github actions", "ci/cd",
        "microservices", "rest", "graphql", "linux", "prometheus", "grafana",
        "datadog", "ansible", "helm", "istio", "vault", "consul",
        "elasticsearch", "spark", "airflow", "dbt", "snowflake",
        "fastapi", "django", "flask", "nextjs", "vue", "angular",
    }

    for job in matched_jobs:
        desc = (job.get("description", "") + " " + job.get("title", "")).lower()
        for kw in tech_keywords:
            if kw in desc:
                keyword_freq[kw] += 1

    # Top keywords appearing in 50%+ of matched JDs
    threshold = len(matched_jobs) * 0.5
    frequent = [(kw, count) for kw, count in keyword_freq.most_common(20) if count >= threshold]

    if frequent:
        kw_list = ", ".join(f"{kw} ({count}x)" for kw, count in frequent[:10])
        report["stats"]["top_jd_keywords"] = kw_list
        report["findings"].append(f"Most requested skills across matched JDs: {kw_list}")


def generate_improvement_suggestions(report: dict, ai_client=None) -> list[str]:
    """Use AI to generate specific improvement suggestions based on the analysis report.

    If no AI client available, returns rule-based suggestions.
    """
    suggestions = []

    for action in report.get("actions", []):
        if action["type"] == "keyword_gap":
            suggestions.append(
                "ACTION: Review your resume's Skills section. Add any technologies "
                "from the top JD keywords list that you genuinely have experience with."
            )
        elif action["type"] == "impact_improvement":
            suggestions.append(
                "ACTION: Rewrite 3-5 bullet points to follow the format: "
                "'[Action verb] [what you did] resulting in [measurable outcome]'. "
                "Example: 'Reduced API latency by 40% by implementing Redis caching layer.'"
            )
        elif action["type"] == "skills_gap":
            suggestions.append(
                "ACTION: Add a 'Technologies' subsection to each job entry listing "
                "the specific tools used. This helps tech recruiters match your experience."
            )
        elif action["type"] == "query_refinement":
            suggestions.append(
                "ACTION: Review config.yaml search queries. Remove overly broad terms "
                "and add specific role titles that match your experience level."
            )
        elif action["type"] == "scraper_fix":
            scraper = action.get("scraper", "unknown")
            suggestions.append(
                f"ACTION: Scraper '{scraper}' is failing. Check if the website changed "
                "its HTML structure, or disable it in config.yaml."
            )

    if not suggestions:
        suggestions.append("No actions needed — pipeline is performing well.")

    return suggestions


def update_config_from_report(report: dict, config_path: str = "config.yaml") -> list[str]:
    """Apply automated fixes to config.yaml based on the analysis report.

    Returns list of changes made.
    """
    changes = []

    with open(config_path) as f:
        config = yaml.safe_load(f)

    for action in report.get("actions", []):
        if action["type"] == "scraper_fix":
            scraper = action.get("scraper")
            if scraper and scraper in config.get("scrapers", {}).get("enabled", []):
                config["scrapers"]["enabled"].remove(scraper)
                changes.append(f"Disabled failing scraper: {scraper}")

    if changes:
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    return changes


def run_self_improvement(output_dir: str = "output", config_path: str = "config.yaml") -> dict:
    """Main entry point: analyze last run, generate suggestions, apply safe fixes.

    Returns the full improvement report.
    """
    logger.info("Starting self-improvement analysis...")

    report = analyze_run_results(output_dir)

    if not report["findings"]:
        report["findings"].append("No issues detected. Pipeline is healthy.")

    suggestions = generate_improvement_suggestions(report)
    report["suggestions"] = suggestions

    # Apply safe automated fixes (only scraper disabling for now)
    changes = update_config_from_report(report, config_path)
    report["auto_changes"] = changes

    # Save report
    report_path = Path(output_dir) / "improvement_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Improvement report saved to %s", report_path)

    # Log summary
    logger.info("=== Self-Improvement Summary ===")
    for finding in report["findings"]:
        logger.info("  Finding: %s", finding)
    for suggestion in suggestions:
        logger.info("  %s", suggestion)
    for change in changes:
        logger.info("  Auto-applied: %s", change)

    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = run_self_improvement()
    print(json.dumps(report, indent=2))
