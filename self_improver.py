"""Self-improving loop for the job hunt pipeline.

Analyzes results from previous runs and generates actionable improvements:
1. Resume quality: identifies weak sections across multiple tailored resumes
2. Matching accuracy: detects false positives/negatives in job matching
3. Scraper health: flags scrapers with low yield or high failure rates
4. Keyword gaps: discovers missing keywords from job descriptions
5. Score trends: tracks improvement/degradation over time
6. Artifact quality: checks compilation success, cover letter compliance, score inflation

After analysis, it updates config.yaml search queries and resume bullet points
to close identified gaps.
"""

import json
import logging
import re
import os
from datetime import datetime, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def analyze_scraper_effectiveness(scraper_stats: dict) -> list[dict]:
    """Rank scrapers by their job-to-match conversion rate.

    Returns list of dicts sorted by match_rate descending:
    [{"source": "adzuna", "jobs_returned": 45, "jobs_matched": 5,
      "match_rate": 0.132, "verdict": "effective"}, ...]

    Verdicts:
    - match_rate > 10%: "highly effective"
    - match_rate 5-10%: "effective"
    - match_rate 1-5%: "low yield"
    - match_rate < 1% or 0 matches: "noise — consider disabling"
    - 0 jobs returned: "broken — investigate"
    """
    ranking = []
    for source, stats in scraper_stats.items():
        jobs_returned = stats.get("jobs_returned", stats.get("count", 0))
        jobs_after_dedup = stats.get("jobs_after_dedup", 0)
        jobs_matched = stats.get("jobs_matched", 0)
        match_rate = stats.get("match_rate", 0)
        avg_score = stats.get("avg_match_score", 0)
        latency = stats.get("latency_seconds", 0)

        # Recompute match_rate if not present (backward compat with old metadata)
        if match_rate == 0 and jobs_returned > 0 and jobs_matched > 0:
            match_rate = round(jobs_matched / jobs_returned, 3)

        # Determine verdict
        if jobs_returned == 0:
            verdict = "broken — investigate"
        elif match_rate > 0.10:
            verdict = "highly effective"
        elif match_rate >= 0.05:
            verdict = "effective"
        elif match_rate >= 0.01:
            verdict = "low yield"
        else:
            verdict = "noise — consider disabling"

        ranking.append({
            "source": source,
            "jobs_returned": jobs_returned,
            "jobs_after_dedup": jobs_after_dedup,
            "jobs_matched": jobs_matched,
            "match_rate": match_rate,
            "avg_match_score": avg_score,
            "latency_seconds": latency,
            "verdict": verdict,
        })

    # Sort by match_rate descending (broken scrapers last)
    ranking.sort(key=lambda x: x["match_rate"], reverse=True)
    return ranking


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

    # Legacy checks: error rates and zero-job scrapers
    for scraper, stats in scraper_stats.items():
        count = stats.get("jobs_returned", stats.get("count", 0))
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

    # Enhanced diagnostics: per-scraper match rate and latency analysis
    ranking = analyze_scraper_effectiveness(scraper_stats)
    report["stats"]["scraper_ranking"] = ranking

    for entry in ranking:
        source = entry["source"]
        verdict = entry["verdict"]
        match_rate = entry["match_rate"]
        latency = entry.get("latency_seconds", 0)

        if verdict == "broken — investigate":
            report["findings"].append(
                f"Scraper '{source}' returned 0 jobs — may be broken or site is down."
            )
            report["actions"].append({
                "type": "scraper_fix",
                "description": f"Investigate scraper '{source}': returned 0 jobs",
                "scraper": source,
            })
        elif verdict == "noise — consider disabling":
            report["findings"].append(
                f"Scraper '{source}' is mostly noise: {entry['jobs_returned']} jobs returned, "
                f"{entry['jobs_matched']} matched ({match_rate:.1%} match rate)."
            )
            report["actions"].append({
                "type": "scraper_low_yield",
                "description": f"Consider disabling scraper '{source}' (match rate {match_rate:.1%})",
                "scraper": source,
            })
        elif verdict == "low yield":
            report["findings"].append(
                f"Scraper '{source}' has low yield: {match_rate:.1%} match rate "
                f"({entry['jobs_matched']}/{entry['jobs_returned']} jobs)."
            )

        if latency > 60:
            report["findings"].append(
                f"Scraper '{source}' is slow: {latency:.0f}s latency."
            )

    # --- 4. Keyword gap analysis ---
    _analyze_keyword_gaps(matched_jobs, report)

    # --- 5. Artifact quality checks ---
    _analyze_artifact_quality(output, report)

    # --- 6. Score inflation detection ---
    _detect_score_inflation(matched_jobs, report)

    return report


def _analyze_artifact_quality(output_dir: Path, report: dict):
    """Check the quality of generated artifacts (PDFs, tex files, cover letters).

    Detects:
    - LaTeX files that failed to compile (no matching PDF)
    - Cover letters with potential dash violations or wrong word counts
    - Resumes with signs of AI fabrication (new bullet points added)
    """
    # Check for tex files without corresponding PDFs (compilation failures)
    tex_files = list(output_dir.glob("*.tex"))
    pdf_files = {p.stem for p in output_dir.glob("*.pdf")}
    compile_failures = []
    for tex in tex_files:
        if tex.stem not in pdf_files:
            compile_failures.append(tex.name)

    if compile_failures:
        failure_rate = len(compile_failures) / max(len(tex_files), 1)
        report["findings"].append(
            f"LaTeX compilation failures: {len(compile_failures)}/{len(tex_files)} "
            f"tex files have no PDF ({failure_rate:.0%} failure rate). "
            f"Files: {', '.join(compile_failures[:5])}"
        )
        if failure_rate > 0.2:
            report["actions"].append({
                "type": "compilation_failure",
                "description": f"High compilation failure rate ({failure_rate:.0%}). "
                "AI models may be generating invalid LaTeX. Check latex_compiler.py logs.",
            })
        report["stats"]["compile_failure_rate"] = round(failure_rate, 3)
    else:
        report["stats"]["compile_failure_rate"] = 0.0

    # Check cover letter quality (scan generated tex for dashes and word count)
    cover_letter_issues = []
    cl_files = [f for f in tex_files if "CoverLetter" in f.name]
    for cl_path in cl_files:
        try:
            content = cl_path.read_text(encoding="utf-8")
            # Extract body text between "Re:" line and "Best regards"
            body_match = re.search(
                r'Re:.*?\n\s*\\vspace\{[^}]+\}\s*\n(.*?)\\vspace\{[^}]+\}\s*\nBest regards',
                content, re.DOTALL
            )
            if body_match:
                body = body_match.group(1).strip()
                # Check for dashes (common AI violation)
                dash_count = body.count(" -- ") + body.count(" --- ") + body.count("\u2014") + body.count("\u2013")
                # Approximate word count
                words = len(body.split())
                if dash_count > 0:
                    cover_letter_issues.append(f"{cl_path.name}: {dash_count} dash(es) in body")
                if words < 200 or words > 500:
                    cover_letter_issues.append(f"{cl_path.name}: body word count {words} (target: 280-380)")
        except Exception:
            pass

    if cover_letter_issues:
        report["findings"].append(
            f"Cover letter quality issues ({len(cover_letter_issues)}): "
            + "; ".join(cover_letter_issues[:5])
        )
        report["stats"]["cover_letter_issues"] = len(cover_letter_issues)


def _detect_score_inflation(matched_jobs: list, report: dict):
    """Detect potential score inflation patterns.

    Flags cases where:
    - All 3 scores are suspiciously identical (AI copied the same number)
    - All scores are 85+ with no variation (rubber-stamping)
    - Average scores are unrealistically high across all jobs
    """
    if not matched_jobs or len(matched_jobs) < 3:
        return

    identical_count = 0
    all_pass_count = 0
    total_avg = 0

    for job in matched_jobs:
        ats = job.get("ats_score", 0)
        hm = job.get("hiring_manager_score", 0)
        tr = job.get("tech_recruiter_score", 0)
        avg = (ats + hm + tr) / 3

        total_avg += avg

        # Check if all 3 scores are exactly identical
        if ats == hm == tr and ats > 0:
            identical_count += 1

        # Check if all 3 are 85+
        if ats >= 85 and hm >= 85 and tr >= 85:
            all_pass_count += 1

    overall_avg = total_avg / len(matched_jobs)
    report["stats"]["overall_avg_score"] = round(overall_avg, 1)

    # Flag suspicious patterns
    if identical_count > len(matched_jobs) * 0.3:
        report["findings"].append(
            f"Score inflation signal: {identical_count}/{len(matched_jobs)} jobs have "
            "identical ATS/HM/TR scores. The scoring model may not be differentiating perspectives."
        )
        report["actions"].append({
            "type": "score_inflation",
            "description": "Scoring model is producing identical scores across perspectives. "
            "Consider switching scoring models or adjusting temperature.",
        })

    if all_pass_count == len(matched_jobs) and len(matched_jobs) > 5:
        report["findings"].append(
            f"Score inflation signal: ALL {len(matched_jobs)} matched jobs scored 85+ "
            "on all 3 perspectives. This is unrealistic and suggests the scorer is too lenient."
        )

    if overall_avg > 90 and len(matched_jobs) > 5:
        report["findings"].append(
            f"Overall average score is {overall_avg:.1f}, which is suspiciously high. "
            "Consider increasing the min_score threshold or using a stricter scoring model."
        )


def _analyze_keyword_gaps(matched_jobs: list, report: dict):
    """Find keywords that appear frequently in JDs but are missing from resumes."""
    if not matched_jobs:
        return

    # Count keyword frequency across matched job descriptions
    from collections import Counter
    keyword_freq = Counter()
    tech_keywords = {
        # Containers & orchestration
        "kubernetes", "docker", "helm", "istio", "service mesh", "ecs", "fargate",
        # IaC & automation
        "terraform", "ansible", "pulumi", "cloudformation", "puppet", "chef",
        # Cloud providers
        "aws", "gcp", "azure", "oracle cloud",
        # Languages
        "python", "go", "golang", "java", "rust", "c++", "ruby",
        "typescript", "javascript", "node", "bash", "sql",
        # Web frameworks
        "react", "nextjs", "vue", "angular", "svelte",
        "fastapi", "django", "flask", "spring boot", "express",
        # Databases
        "postgresql", "mysql", "mongodb", "redis", "dynamodb", "cassandra",
        "elasticsearch", "opensearch", "supabase", "firestore",
        # Data & ML
        "spark", "airflow", "dbt", "snowflake", "kafka", "rabbitmq",
        "machine learning", "llm", "langchain", "rag",
        # CI/CD
        "jenkins", "github actions", "ci/cd", "gitlab ci", "argocd", "flux",
        # Observability
        "prometheus", "grafana", "datadog", "splunk", "new relic",
        "opentelemetry", "jaeger", "pagerduty",
        # Security
        "vault", "consul", "sso", "oauth", "iam",
        # Practices
        "microservices", "rest", "graphql", "grpc", "linux", "agile", "scrum",
        "sre", "devops", "platform engineering", "gitops",
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


def analyze_model_quality(report: dict):
    """Analyze AI model quality from the quality log and generate model rankings.

    Reads output/ai_quality_log.jsonl, computes per-model stats, and:
    1. Adds model rankings to the report
    2. Generates output/preferred_models.json for ai_client.py to read
    """
    from quality_logger import get_model_stats

    stats = get_model_stats()
    if not stats:
        report["findings"].append("No AI quality data found — run the pipeline first to generate quality metrics.")
        return

    # Rank by average score (descending)
    ranked = sorted(stats.items(), key=lambda x: x[1]["avg_score"], reverse=True)

    report["stats"]["model_rankings"] = [
        {
            "model": key,
            "avg_score": s["avg_score"],
            "count": s["count"],
            "errors": s["errors"],
            "tasks": s["tasks"],
        }
        for key, s in ranked
    ]

    # Generate findings
    if ranked:
        best = ranked[0]
        worst = ranked[-1]
        report["findings"].append(
            f"Best performing model: {best[0]} (avg score {best[1]['avg_score']}, {best[1]['count']} artifacts)"
        )
        if worst[1]["avg_score"] < 60 and worst[1]["count"] >= 3:
            report["findings"].append(
                f"Underperforming model: {worst[0]} (avg score {worst[1]['avg_score']}) — consider removing from council"
            )

    # Models with high error rates
    for key, s in stats.items():
        if s["count"] > 0 and s["errors"] / s["count"] > 0.3:
            report["findings"].append(
                f"Model {key} has {s['errors']}/{s['count']} errors ({s['errors']/s['count']:.0%}) — unreliable"
            )

    # Generate preferred_models.json
    # This file ranks model identifiers by quality so ai_client.py can reorder its provider chain
    preferred = {
        "generated_at": datetime.now().isoformat(),
        "rankings": [
            {"provider_model": key, "avg_score": s["avg_score"], "sample_count": s["count"]}
            for key, s in ranked
            if s["count"] >= 2  # Need at least 2 samples to rank
        ],
        "blacklist": [
            key for key, s in stats.items()
            if s["count"] >= 3 and (s["avg_score"] < 40 or s["errors"] / max(s["count"], 1) > 0.5)
        ],
    }

    preferred_path = Path("output/preferred_models.json")
    preferred_path.parent.mkdir(parents=True, exist_ok=True)
    with open(preferred_path, "w") as f:
        json.dump(preferred, f, indent=2)

    logger.info(f"[SELF-IMPROVE] Model rankings saved to {preferred_path}")
    if preferred["blacklist"]:
        logger.warning(f"[SELF-IMPROVE] Blacklisted models: {preferred['blacklist']}")


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
        elif action["type"] == "scraper_low_yield":
            scraper = action.get("scraper", "unknown")
            suggestions.append(
                f"ACTION: Scraper '{scraper}' produces mostly irrelevant jobs. "
                "Consider disabling it or adjusting its search queries to improve signal-to-noise ratio."
            )
        elif action["type"] == "compilation_failure":
            suggestions.append(
                "ACTION: High LaTeX compilation failure rate. Check output/pipeline.log for "
                "tectonic/pdflatex errors. Common causes: AI-generated unbalanced braces, "
                "missing \\end{document}, or corrupted macro definitions."
            )
        elif action["type"] == "score_inflation":
            suggestions.append(
                "ACTION: Scoring model is rubber-stamping scores. Consider: "
                "(1) switching to a different scoring model, "
                "(2) raising min_score threshold from 60 to 65, "
                "(3) ensuring the scorer and tailor use different models to avoid self-evaluation bias."
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
    analyze_model_quality(report)

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


def generate_adjustments(
    scraper_stats: dict = None,
    score_stats: dict = None,
    quality_stats: dict = None,
    model_stats: dict = None,
    keyword_stats: dict = None,
) -> list[dict]:
    """Analyze pipeline metrics and generate tiered adjustments.

    Returns a list of adjustment dicts, each with:
    - adjustment_type: what kind of change (scraper_config, score_threshold, prompt_change, ...)
    - risk_level: "low" | "medium" | "high"
    - status: "auto_applied" (low/medium) or "pending" (high, needs human review)
    - payload: the concrete change to apply
    - reason: human-readable explanation
    - evidence: raw data that triggered this adjustment
    """
    adjustments = []

    # Low-risk: disable broken scrapers (3-day zero yield)
    if scraper_stats:
        for name, stats in scraper_stats.items():
            yields = stats.get("yields", [])
            if len(yields) >= 3 and all(y == 0 for y in yields[-3:]):
                adjustments.append({
                    "adjustment_type": "scraper_config",
                    "risk_level": "low",
                    "status": "auto_applied",
                    "payload": {"scraper": name, "action": "disable"},
                    "reason": f"Scraper '{name}' returned 0 jobs for 3 consecutive runs",
                    "evidence": {"yields": yields},
                })

    # Low-risk: reorder sources by historical match rate
    if scraper_stats:
        source_rates = {}
        for name, stats in scraper_stats.items():
            yields = stats.get("yields", [])
            matched = stats.get("matched", [])
            if yields and matched and len(yields) == len(matched):
                total_yielded = sum(yields)
                total_matched = sum(matched)
                if total_yielded > 0:
                    source_rates[name] = round(total_matched / total_yielded, 3)
        if source_rates:
            ordered = sorted(source_rates, key=lambda k: source_rates[k], reverse=True)
            adjustments.append({
                "adjustment_type": "source_order",
                "risk_level": "low",
                "status": "auto_applied",
                "payload": {"sources_ranked": ordered, "match_rates": source_rates},
                "reason": "Reorder sources by historical match rate to prioritise high-signal scrapers",
                "evidence": {"source_rates": source_rates},
            })

    # Medium-risk: score threshold adjustment (too many low-scoring jobs)
    if score_stats:
        if score_stats.get("pct_below_50", 0) > 0.8:
            adjustments.append({
                "adjustment_type": "score_threshold",
                "risk_level": "medium",
                "status": "auto_applied",
                "notify": True,
                "payload": {"min_match_score": max(30, score_stats.get("avg_score", 50) - 10)},
                "previous_state": {"min_match_score": 50},
                "reason": (
                    f"{score_stats['pct_below_50']*100:.0f}% of jobs scored below 50"
                    " — possible query-resume misalignment"
                ),
                "evidence": score_stats,
            })

        # Medium-risk: flag when all jobs cluster in C/D tiers (no S/A)
        tier_dist = score_stats.get("tier_distribution", {})
        high_tier_count = tier_dist.get("S", 0) + tier_dist.get("A", 0)
        total = score_stats.get("total", 0)
        if total >= 10 and high_tier_count == 0:
            adjustments.append({
                "adjustment_type": "score_threshold",
                "risk_level": "medium",
                "status": "auto_applied",
                "notify": True,
                "payload": {"min_match_score": max(40, score_stats.get("avg_score", 60) - 15)},
                "previous_state": {"min_match_score": 60},
                "reason": (
                    f"Zero S/A tier jobs in last 7 days ({total} scored). "
                    "Threshold may be too strict or queries need refinement."
                ),
                "evidence": tier_dist,
            })

    # High-risk: prompt change suggestion (writing quality declining or high compile failure)
    if quality_stats:
        if quality_stats.get("trend") == "declining":
            avg_last = quality_stats.get("avg_last_3") or 0
            avg_prev = quality_stats.get("avg_prev_3") or 0
            fail_rate = quality_stats.get("compile_fail_rate", 0)

            if avg_prev > 0 and (avg_prev - avg_last) / avg_prev > 0.1:
                adjustments.append({
                    "adjustment_type": "prompt_change",
                    "risk_level": "high",
                    "status": "pending",
                    "payload": {"target": "tailoring_prompt", "action": "review_and_update"},
                    "reason": (
                        f"Writing quality declining: avg {avg_last:.1f} (last 3 runs)"
                        f" vs {avg_prev:.1f} (previous 3)"
                    ),
                    "evidence": quality_stats,
                })
            elif fail_rate > 0.2:
                adjustments.append({
                    "adjustment_type": "prompt_change",
                    "risk_level": "high",
                    "status": "pending",
                    "payload": {"target": "tailoring_prompt", "action": "review_and_update"},
                    "reason": (
                        f"LaTeX compilation failure rate is {fail_rate:.0%} — "
                        "AI tailoring prompt may be generating invalid LaTeX"
                    ),
                    "evidence": quality_stats,
                })

    return adjustments


def detect_conflicts(adjustments: list[dict]) -> list[dict]:
    """Detect contradictory adjustments that set the same key to different values.

    Only considers adjustments with status "auto_applied" or "approved".
    Returns a list of conflict dicts with the conflicting key, both adjustment IDs,
    and their respective values.
    """
    key_values = {}
    conflicts = []
    for adj in adjustments:
        if adj.get("status") not in ("auto_applied", "approved"):
            continue
        for key, value in (adj.get("payload") or {}).items():
            if key in key_values and key_values[key]["value"] != value:
                conflicts.append({
                    "key": key,
                    "adjustment_a": key_values[key]["id"],
                    "value_a": key_values[key]["value"],
                    "adjustment_b": adj.get("id", "new"),
                    "value_b": value,
                })
            key_values[key] = {"id": adj.get("id", "new"), "value": value}
    return conflicts


def analyze_query_effectiveness(query_stats: dict, threshold: float = 0.05, min_runs: int = 3) -> list[dict]:
    """Flag search queries with consistently low match rates."""
    suggestions = []
    for query, stats in query_stats.items():
        rates = stats.get("match_rates", [])
        if len(rates) >= min_runs and all(r < threshold for r in rates[-min_runs:]):
            suggestions.append({
                "adjustment_type": "keyword_weight",
                "risk_level": "medium",
                "status": "auto_applied",
                "notify": True,
                "payload": {"query": query, "action": "suggest_modification"},
                "reason": f"Query '{query}' has <{threshold*100:.0f}% match rate for {min_runs}+ consecutive runs",
                "evidence": {"match_rates": rates},
            })
    return sorted(suggestions, key=lambda s: min(s["evidence"]["match_rates"]))


def analyze_keyword_gaps_for_resume(keyword_stats: dict, min_jobs: int = 25) -> list[dict]:
    """Suggest base resume updates for consistently missing keywords.

    When keyword gap analysis across many matched JDs shows a skill appearing
    in ``min_jobs`` or more descriptions, it likely belongs on the base resume.
    Each suggestion is a medium-risk quality flag that the user reviews on the
    dashboard before approving changes to their base resume.

    Args:
        keyword_stats: Mapping of keyword -> {"count": int, "avg_job_score": float}.
        min_jobs: Minimum number of JD appearances to trigger a suggestion.

    Returns:
        List of adjustment dicts sorted by count descending.
    """
    suggestions = []
    for keyword, stats in keyword_stats.items():
        if stats["count"] >= min_jobs:
            suggestions.append({
                "adjustment_type": "quality_flag",
                "risk_level": "medium",
                "status": "auto_applied",
                "notify": True,
                "payload": {"keyword": keyword, "action": "add_to_base_resume"},
                "reason": f"Consider adding '{keyword}' to base resume — appeared in {stats['count']} of top matched JDs (avg score: {stats['avg_job_score']})",
                "evidence": stats,
            })
    return sorted(suggestions, key=lambda s: s["evidence"]["count"], reverse=True)


def should_revert_adjustment(adjustment: dict, run_metrics: list[dict], threshold: float = 0.05) -> bool:
    """Check if an adjustment worsened metrics over 3+ runs.

    Compares the average base score from the 3 runs after the adjustment was applied
    against the baseline (run before the adjustment). If the average dropped by more
    than ``threshold`` (as a fraction of the baseline), returns True.

    Args:
        adjustment: The adjustment dict (unused but kept for interface consistency).
        run_metrics: List of run metric dicts ordered chronologically. Index 0 is
            the baseline (before adjustment), indices 1-3 are post-adjustment runs.
        threshold: Fractional decline that triggers a revert (default 5%).

    Returns:
        True if the adjustment should be reverted.
    """
    if len(run_metrics) < 4:
        return False
    before = run_metrics[0].get("avg_base_score", 0)
    after_avg = sum(r.get("avg_base_score", 0) for r in run_metrics[1:4]) / 3
    if before == 0:
        return False
    change = (after_avg - before) / before
    return change < -threshold


def should_revert_or_extend(adjustment: dict, run_metrics: list[dict], threshold: float = 0.05) -> str:
    """Extended evaluation: 3 runs -> revert/confirm. Inconclusive -> extend to 5.

    Decision logic:
    - Fewer than 4 data points: "wait" (not enough data).
    - 3-run average dropped > threshold: "revert".
    - 3-run average improved > threshold: "confirm".
    - Inconclusive (change within +/- threshold):
        - If 6+ data points available, evaluate 5-run average. Drop -> "revert", else "confirm".
        - Otherwise: "extend" (need more runs).

    Args:
        adjustment: The adjustment dict (unused but kept for interface consistency).
        run_metrics: Chronological list of run metric dicts. Index 0 = baseline.
        threshold: Fractional change threshold (default 5%).

    Returns:
        One of "wait", "revert", "confirm", or "extend".
    """
    if len(run_metrics) < 4:
        return "wait"
    before = run_metrics[0].get("avg_base_score", 0)
    if before == 0:
        return "wait"

    after_3 = sum(r.get("avg_base_score", 0) for r in run_metrics[1:4]) / 3
    change_3 = (after_3 - before) / before

    if change_3 < -threshold:
        return "revert"
    if change_3 > threshold:
        return "confirm"

    # Inconclusive at 3 runs — try 5 if available
    if len(run_metrics) >= 6:
        after_5 = sum(r.get("avg_base_score", 0) for r in run_metrics[1:6]) / 5
        change_5 = (after_5 - before) / before
        if change_5 < -threshold:
            return "revert"
        return "confirm"

    return "extend"


def is_on_cooldown(adjustment: dict) -> bool:
    """Check if a reverted adjustment is still in its cooldown period.

    Only reverted adjustments can be on cooldown. Compares the current time
    against the ``cooldown_until`` ISO timestamp stored on the adjustment.

    Args:
        adjustment: Adjustment dict with at least "status" and optionally
            "cooldown_until" fields.

    Returns:
        True if the adjustment is reverted and the cooldown has not expired.
    """
    if adjustment.get("status") != "reverted":
        return False
    cooldown_until = adjustment.get("cooldown_until")
    if cooldown_until:
        return datetime.now().isoformat() < cooldown_until
    return False


def execute_revert(db, adjustment: dict, cooldown_runs: int = 5):
    """Revert an adjustment and set a cooldown period.

    Marks the adjustment as "reverted" in the database and, if the adjustment
    stored its ``previous_state``, inserts a new auto-applied adjustment that
    restores the prior configuration.

    Args:
        db: Supabase client instance.
        adjustment: The adjustment dict to revert (must have "id" and "user_id").
        cooldown_runs: Number of days for the cooldown period (default 5).
    """
    cooldown_until = (datetime.now() + timedelta(days=cooldown_runs)).isoformat()
    db.table("pipeline_adjustments").update({
        "status": "reverted",
        "reverted_at": datetime.now().isoformat(),
        "cooldown_until": cooldown_until,
    }).eq("id", adjustment["id"]).execute()

    if adjustment.get("previous_state"):
        db.table("pipeline_adjustments").insert({
            "user_id": adjustment["user_id"],
            "adjustment_type": adjustment["adjustment_type"],
            "risk_level": "low",
            "status": "auto_applied",
            "payload": adjustment["previous_state"],
            "reason": f"Auto-revert of adjustment {adjustment['id']}",
            "evidence": {"reverted_from": adjustment["id"]},
        }).execute()


def save_pipeline_run(db, user_id: str, run_data: dict):
    """Save pipeline run metrics to Supabase.

    Inserts a row into the ``pipeline_runs`` table with timing information,
    job counts, score averages, and per-scraper/model statistics.

    Args:
        db: Supabase client instance.
        user_id: The user who owns this pipeline run.
        run_data: Dict with run metrics (jobs_scraped, avg_base_score, etc.).
    """
    db.table("pipeline_runs").insert({
        "user_id": user_id,
        "started_at": run_data.get("started_at"),
        "completed_at": datetime.now().isoformat(),
        "jobs_scraped": run_data.get("jobs_scraped", 0),
        "jobs_new": run_data.get("jobs_new", 0),
        "jobs_scored": run_data.get("jobs_scored", 0),
        "jobs_matched": run_data.get("jobs_matched", 0),
        "jobs_tailored": run_data.get("jobs_tailored", 0),
        "avg_base_score": run_data.get("avg_base_score"),
        "avg_final_score": run_data.get("avg_final_score"),
        "avg_writing_quality": run_data.get("avg_writing_quality"),
        "active_adjustments": run_data.get("active_adjustments"),
        "scraper_stats": run_data.get("scraper_stats"),
        "model_stats": run_data.get("model_stats"),
        "status": "completed",
    }).execute()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = run_self_improvement()
    print(json.dumps(report, indent=2))
