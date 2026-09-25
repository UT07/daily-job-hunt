"""Tests for retrieval.bullets.

IMPORTANT DEVIATION FROM THE TASK-16 BRIEF, found by inspecting real data
before implementing (per the task's own instruction to verify, not trust,
the brief's regexes): the brief's fixture and extract_bullets() assumed
`\\section{...}` headings and a `\\resumeItem{...}` bullet macro. Neither
exists ANYWHERE in this repo -- `grep -rl resumeItem` across the whole tree
(including old output/ dirs and worktrees) returns zero hits. The two real
base resumes (resumes/fullstack.tex, resumes/sre_devops.tex) and the two
live rows in Supabase's user_resumes.tex_content (verbatim copies of those
same files -- see db_client.py's user_resumes helpers) all use:

  - `\\section*{Name}` -- WITH the star (titlesec's \\titleformat{\\section}
    in the resume preamble renders it identically to an unstarred one, but
    the macro actually used is starred).
  - plain `\\begin{itemize}...\\end{itemize}` blocks of `\\item ...` bullets
    -- the exact convention tailor_resume.py._check_required_sections and
    tailorer.py.extract_base_sections/_extract_itemize_bullets already parse
    elsewhere in this codebase.

Had the brief's regexes shipped unmodified, extract_bullets() would have
returned [] against every real resume (0 for 2 on the live user_resumes
rows), index_bullets() would have indexed 0 rows, and retrieve_evidence()
would always come back empty -- a no-op safety control that still passes
every one of the brief's own tests, because those tests were written
against the brief's own invented macro. The fixtures below were adapted to
this repo's real `\\section*` / itemize+`\\item` shape so the unit tests
actually exercise the parser real resumes will hit. See
test_extract_bullets_on_real_resume below for the belt-and-suspenders check
against an actual, verbatim resume fixture.
"""
from unittest.mock import MagicMock, patch

from retrieval import bullets

TEX = r"""
\section*{Experience}
\begin{itemize}
  \item Built a Python service handling 2M requests/day on AWS Lambda.
  \item Cut p95 latency 40\% by batching downstream calls.
\end{itemize}
\section*{Projects}
\begin{itemize}
  \item Shipped a LaTeX resume pipeline with 3-perspective AI scoring.
\end{itemize}
"""


def test_extract_bullets_finds_every_resume_item():
    out = bullets.extract_bullets(TEX)
    assert len(out) == 3


def test_extract_bullets_attributes_the_right_section():
    out = bullets.extract_bullets(TEX)
    assert out[0]["section"] == "Experience"
    assert out[2]["section"] == "Projects"


def test_extract_bullets_unescapes_latex_percent():
    out = bullets.extract_bullets(TEX)
    assert "40%" in out[1]["text"]


def test_extract_bullets_ignores_empty_items():
    assert bullets.extract_bullets(r"\section*{X}\begin{itemize}\item \end{itemize}") == []


def test_extract_bullets_strips_inline_formatting():
    # Real bullets are full of \textbf{}/\href{}{} markup (see
    # resumes/fullstack.tex); stored/embedded text should read as plain
    # English, not raw LaTeX source.
    tex = (
        r"\section*{Experience}\begin{itemize}"
        r"\item Built \textbf{8 services} via \href{https://x.com}{a link}."
        r"\end{itemize}"
    )
    out = bullets.extract_bullets(tex)
    assert out[0]["text"] == "Built 8 services via a link."


def test_extract_bullets_handles_nested_braces_in_href():
    # Regression for a real defect found while verifying this parser against
    # resumes/fullstack.tex's Certifications section: \href{url}{\textbf{
    # \textit{Name}}} nests TWO levels of braces inside \href's second
    # argument. A flat `[^}]*` regex stops at the first `}` it sees, so the
    # naive version of this stripper matched only through the \textit{...}
    # closing brace, silently dropping the certification's name entirely and
    # keeping just the trailing "Issued <date>" text -- a silent data-loss
    # bug in the exact feature meant to ground tailoring in real facts.
    tex = (
        r"\section*{Certifications}\begin{itemize}"
        r"\item \href{https://example.com/badge}{\textbf{\textit{AWS Certified "
        r"Solutions Architect -- Professional (SAP-C02)}}} \hfill \textit{Issued Mar 2024}"
        r"\end{itemize}"
    )
    out = bullets.extract_bullets(tex)
    assert len(out) == 1
    assert "AWS Certified Solutions Architect" in out[0]["text"]
    assert "Issued Mar 2024" in out[0]["text"]


def test_retrieve_evidence_passes_k_through():
    with patch.object(bullets, "embed", return_value=[0.1] * 768), \
         patch.object(bullets, "similar_bullets", return_value=[{"text": "a"}]) as m:
        bullets.retrieve_evidence("u1", "jd text", k=8)
    assert m.call_args.kwargs["k"] == 8


def test_index_bullets_returns_count_indexed():
    with patch.object(bullets, "embed_batch", return_value=[[0.1] * 768] * 3), \
         patch.object(bullets, "_insert_bullets") as ins:
        assert bullets.index_bullets("u1", TEX, "r1") == 3
    assert len(ins.call_args[0][0]) == 3


def test_index_bullets_skips_embedding_call_when_resume_has_no_bullets():
    with patch.object(bullets, "embed_batch", side_effect=AssertionError("must not embed")), \
         patch.object(bullets, "_insert_bullets", side_effect=AssertionError("must not insert")):
        assert bullets.index_bullets("u1", r"\section*{Empty}", "r1") == 0


def test_insert_bullets_writes_to_resume_bullets_table():
    # _insert_bullets is mocked out of the two tests above (it's the one
    # function in this module that talks to Supabase); cover its own body
    # here instead, the way test_retrieval_store.py covers store._db()
    # callers by mocking store._db rather than the network.
    db = MagicMock()
    rows = [{"user_id": "u1", "section": "Experience", "text": "did a thing"}]
    with patch.object(bullets, "ai_helper") as mock_ai_helper:
        mock_ai_helper.get_supabase.return_value = db
        bullets._insert_bullets(rows)
    db.table.assert_called_once_with("resume_bullets")
    db.table.return_value.insert.assert_called_once_with(rows)
    db.table.return_value.insert.return_value.execute.assert_called_once()


# ---------------------------------------------------------------------------
# Real-resume regression test (per task-16 instructions): a verbatim,
# byte-for-byte excerpt of resumes/fullstack.tex (everything from
# \section*{Summary} through \end{document} -- the preamble above it defines
# macros/packages and contains no \section or \item tokens, so it's omitted
# here for length only, not to dodge anything). This is frozen into the test
# file itself rather than read from disk at test time, so a future edit to
# resumes/fullstack.tex can't silently make this test stop testing what it
# claims to test.
#
# Expected bullet count (35) and the by-section breakdown were verified two
# ways before being pinned here: (a) hand-counting every \item in this exact
# excerpt, and (b) an independent regex sanity-check (`\item\s` occurrence
# count) run directly against the live user_resumes.tex_content row for this
# same resume in Supabase -- both gave 35. This is the test that would have
# caught the brief's \resumeItem/\section mismatch: against this fixture,
# the brief's original extract_bullets() returns 0, not 35.
# ---------------------------------------------------------------------------

REAL_RESUME_TEX = r"""
\section*{Summary}
Full-stack software engineer with \textbf{3+ years} of experience building, shipping, and operating scalable web applications and cloud-native services. Proficient in \textbf{Python} and \textbf{TypeScript/React} across the stack -- from designing RESTful APIs and data-driven backends to building responsive, component-based UIs. Experienced in integrating \textbf{AI/LLM capabilities} into production applications and working directly with customers to iterate in live environments. Strong foundation in data structures, algorithms, design patterns, and object-oriented architecture. Proven track record of owning features end-to-end -- from design through delivery -- with automated testing, CI/CD, and production-grade observability baked in. \textbf{MSc Cloud Computing}; \textbf{AWS Solutions Architect -- Professional}. Dublin-based (Stamp 1G) -- eligible for full-time employment in Ireland.
%==================== TECHNICAL SKILLS ====================
\section*{Technical Skills}
\begin{itemize}
  \item \textbf{Languages \& Frameworks:} Python (FastAPI, Flask, Django), TypeScript/JavaScript (React 19, React Native, Node.js/Express), Java, SQL, Bash
  \item \textbf{Front-End:} React, React Native, Tailwind CSS, Zustand/Redux, Framer Motion; responsive design, component architecture, state management, A/B testing
  \item \textbf{Databases \& Data:} PostgreSQL, MySQL, DynamoDB, Redis, Firestore, Supabase; schema design, query optimization, ETL, PySpark, data pipelines
  \item \textbf{AI \& ML:} LLM integration (multi-model consensus, prompt engineering), ONNX inference, Gemini API, scikit-learn, TensorFlow/Keras, NLP, RAG pipelines
  \item \textbf{Testing:} Jest (3,000+ tests), Maestro (E2E), pytest, Playwright, JUnit; SAST/DAST (SonarCloud, OWASP ZAP); TDD, code review
  \item \textbf{CI/CD \& DevOps:} GitHub Actions, Jenkins, AWS CodePipeline, EAS Build; automated test gates, blue/green and canary deployments
  \item \textbf{Cloud:} AWS (EC2, ECS/Fargate, EKS, Lambda, RDS, S3, API Gateway, SQS/SNS, CloudFront, Route\,53), Firebase, Supabase, Docker, Kubernetes/Helm, Terraform
  \item \textbf{Observability \& Practices:} Prometheus/Grafana, CloudWatch, PostHog, X-Ray; SLO/SLI tracking, distributed tracing; Git, Linux, microservices, agile, GDPR
\end{itemize}
%==================== EXPERIENCE ====================
\section*{Experience}
\jobentry{Clover IT Services}{New York, NY (Remote)}{Jun 2022 -- Jul 2024}{\textbf{\textit{Software Engineer / Site Reliability Engineer}}}
\begin{itemize}
  \item Designed, developed, and maintained \textbf{8 production microservices} across a multi-region AWS SaaS platform; owned features end-to-end from requirements through implementation, testing, and production deployment.
  \item Built backend services in \textbf{Python} (FastAPI, Flask) and \textbf{Node.js}, implementing RESTful APIs, data orchestration layers, and event-driven integrations (SQS/SNS) across 3 AWS regions.
  \item Developed reusable front-end components and internal tooling UIs, collaborating with product managers to translate requirements into well-architected user experiences.
  \item Established \textbf{pytest and Jest test gates} across 10+ services in CI/CD pipelines; reduced release lead time from 3 days to \textbf{4--6 hours (85\% improvement)}.
  \item Architected full-stack observability (Prometheus/Grafana, CloudWatch, X-Ray) with SLO/SLI tracking; reduced \textbf{MTTR by 35\%} through proactive alerting and root cause analysis.
  \item Designed self-service infrastructure using Terraform modules and containerised deployments (Docker, EKS/Helm); sustained \textbf{99.9\% uptime} and delivered \textbf{22\% infrastructure cost reduction}.
  \item \textbf{Promoted to team lead within 14 months}; mentored 3--4 engineers, led design reviews, and championed engineering best practices.
\end{itemize}
\jobentry{Seattle Kraken (NHL)}{Seattle, WA}{Jun 2021 -- May 2022}{\textbf{\textit{Data Engineering Intern}}}
\begin{itemize}
  \item Built automated data pipelines in \textbf{Python} (PySpark, Azure Data Factory) for analytics ingestion and validation; reduced weekly report preparation time by \textbf{30\%} across 500K+ records supporting real-time dashboards.
  \item Developed internal reporting tools and interactive dashboards using \textbf{React} and D3.js, enabling non-technical stakeholders to self-serve analytics without engineering involvement.
  \item Implemented data quality checks catching \textbf{15+} silent data corruption incidents before production; partnered with engineering to debug ETL reliability and improve data freshness.
\end{itemize}
%==================== PROJECTS ====================
% Force Featured Projects to always start on a new page. Resolves the
% "section header on page 1, content on page 2" awkward split that has
% been showing up in tailored outputs without per-job custom spacing.
\clearpage
\section*{Featured Projects}
\projectentryurl{Purrrfect Keys -- AI Piano Learning App}{Jan 2026 -- Present}{https://expo.dev/accounts/ut254/projects/purrrfect-keys/builds/229bd252-c684-4af5-b39f-4f3623455a7e}{expo.dev/accounts/ut254/projects/purrrfect-keys}{React Native, TypeScript, Firebase, Gemini AI, ONNX, Jest, Maestro}
\begin{itemize}
  \item Shipped a cross-platform piano learning app through \textbf{14 development phases} with real-time pitch detection under \textbf{10ms}, AI-generated lessons via Gemini, competitive league with MMR ranking, and offline-first sync across 12 data types. \textbf{3,043 tests} passing across 149 suites.
  \item Designed the full architecture (React Native + Firebase + 9 Cloud Functions), built CI/CD with EAS Build, integrated PostHog analytics, and implemented gamification that drives daily active usage through cat avatar progression and streak rewards.
\end{itemize}
\vspace{0.20em}
\projectentryurl{WhatsTheCraic -- Live Event Discovery Platform}{Apr 2025 -- Jul 2025}{https://github.com/UT07/whatsthecraic}{whatsthecraic.run.place}{Node.js, React, FastAPI, MySQL, DynamoDB, Docker, AWS, Spotify OAuth}
\begin{itemize}
  \item Launched a live event platform that improved event discovery conversion by \textbf{18\%} through A/B-tested personalized ranking powered by Spotify listening data. Ingested events from Ticketmaster and Eventbrite with deduplication into a canonical data model.
  \item Designed and deployed \textbf{6 microservices} (aggregator, events, DJ, venue, auth, frontend) on AWS with Docker Compose, auto-TLS, and health/metrics instrumentation across 4 subdomains. Added ICS calendar export and Spotify OAuth for preference-based recommendations.
\end{itemize}
\vspace{0.20em}
\projectentryurl{Genomic Benchmarking Platform -- MSc Thesis}{Sep 2025 -- Dec 2025}{https://github.com/UT07/Performance-Optimization-and-Cost-Analysis-of-qpAdm-Genomic-Modeling}{github.com/UT07/qpAdm-Genomic-Benchmarking}{Terraform, Docker, Snakemake, EC2 Auto Scaling, S3, CloudWatch}
\begin{itemize}
  \item Ran \textbf{10,700+ parallel genomic workloads} on AWS with zero manual intervention. Terraform provisioned Auto Scaling Groups that launched workers, executed containerised jobs, emitted CloudWatch metrics, and self-terminated automatically.
  \item Cut compute costs by \textbf{66\%} using Spot Instances and eliminated \textbf{99\%} of preprocessing overhead through data precomputation. Found the optimal instance configuration at \textbf{\$0.006 per run}.
\end{itemize}
\vspace{0.20em}
\projectentryurl{NaukriBaba -- AI-Powered Job Automation SaaS}{Mar 2026 -- Present}{https://github.com/UT07/naukribaba}{github.com/UT07/naukribaba}{Python, FastAPI, React, Supabase, LaTeX, AWS Lambda, Playwright}
\begin{itemize}
  \item Automated the entire job search pipeline: scrapes \textbf{8 job boards} daily, scores candidates from 3 perspectives (ATS, Hiring Manager, Tech Recruiter) using a \textbf{consensus council of 32 LLMs}, generates tailored resumes and cover letters, and emails results with S3-hosted PDFs.
  \item Built as a multi-tenant SaaS: React dashboard with Supabase Auth and Row Level Security, GDPR-compliant data export/deletion, and a self-improvement loop that tracks per-model quality and deprioritizes underperforming AI models over time.
\end{itemize}
\vspace{0.20em}
\projectentryurl{UTWorld -- Dual-Mode Portfolio with CMS}{2024 -- Present}{https://utworld.netlify.app}{utworld.netlify.app}{React 19, FastAPI, SQLAlchemy, Neon PostgreSQL, AWS Lambda, S3, CloudFront}
\begin{itemize}
  \item Built a portfolio platform serving two distinct audiences (Professional and DJ) from a single codebase with mode switching, API-backed CMS, and an admin panel for managing projects, media, gigs, and resume uploads. S3 assets served via CloudFront CDN with static JSON fallback for resilience.
  \item Deployed as \textbf{3 independent services}: React 19 public site on Netlify, React 18 admin panel, and FastAPI REST API on AWS Lambda via SAM with CI/CD through GitHub Actions. Includes Pytest API tests and Playwright E2E coverage.
\end{itemize}
%==================== EDUCATION ====================
\section*{Education}
\textbf{National College of Ireland}, Dublin, Ireland \hfill \textit{Sep 2024 -- Jan 2026}\\[-0.15em]
\textbf{\textit{MSc Cloud Computing}}\\[-0.25em]
\begin{itemize}
  \item \textbf{Coursework:} Cloud Architectures, Cloud DevOpsSec, Scalable Cloud Programming, Cloud Machine Learning, Data Governance/Compliance/Ethics, Research in Computing.
\end{itemize}
\vspace{0.20em}
\textbf{Southern New Hampshire University}, Manchester, NH \hfill \textit{Aug 2023 -- May 2024}\\[-0.15em]
\textbf{\textit{BS Computer Science}}\\[-0.25em]
\begin{itemize}
  \item \textbf{Coursework:} Software Dev Lifecycle, Full-Stack Development, Database Systems, Software Testing.
\end{itemize}
\vspace{0.20em}
\textbf{The University of Texas at Arlington}, Arlington, TX \hfill \textit{Aug 2019 -- May 2022}\\[-0.15em]
\textbf{\textit{BS Software Engineering (Coursework, Transferred)}}\\[-0.25em]
\begin{itemize}
  \item \textbf{Coursework:} Algorithms \& DS, Operating Systems, Computer Networks, Information Security.
  \item \textbf{Teaching Assistant:} CSE 3318 Data Structures \& Algorithms (50+ students).
\end{itemize}
%==================== CERTIFICATIONS ====================
\section*{Certifications}
\begin{itemize}
  \item \href{https://www.credly.com/badges/6e22a6c0-9922-49d5-b59f-34cc593c82c3/public_url}{\textbf{\textit{AWS Certified Solutions Architect -- Professional (SAP-C02)}}} \hfill \textit{Issued Mar 2024}
  \item \href{https://www.credly.com/badges/84671bf4-ce9f-4d8b-a0de-0ef606ad5646/public_url}{\textbf{\textit{AWS Certified Developer -- Associate (DVA-C01)}}} \hfill \textit{Issued Sep 2022}
  \item \href{https://www.credly.com/badges/e671b9de-e72a-48cb-82fc-33776c285174/public_url}{\textbf{\textit{AWS Certified Cloud Practitioner (CLF-C01)}}} \hfill \textit{Issued Jun 2022}
\end{itemize}
\end{document}
"""


def test_extract_bullets_on_real_resume():
    out = bullets.extract_bullets(REAL_RESUME_TEX)
    assert len(out) == 35


def test_extract_bullets_on_real_resume_section_breakdown():
    from collections import Counter
    out = bullets.extract_bullets(REAL_RESUME_TEX)
    counts = Counter(b["section"] for b in out)
    assert counts == {
        "Technical Skills": 8,
        "Experience": 10,
        "Featured Projects": 10,
        "Education": 4,
        "Certifications": 3,
    }


def test_extract_bullets_on_real_resume_clean_bullet_text_is_pinned():
    # A simple, single-level-braces bullet, pinned verbatim as a concrete
    # regression check (not just a count) -- this is what an LLM tailoring
    # prompt would actually see as grounding evidence.
    out = bullets.extract_bullets(REAL_RESUME_TEX)
    kraken_bullets = [b for b in out if "silent data corruption" in b["text"]]
    assert len(kraken_bullets) == 1
    assert kraken_bullets[0]["section"] == "Experience"
    assert kraken_bullets[0]["text"] == (
        "Implemented data quality checks catching 15+ silent data corruption "
        "incidents before production; partnered with engineering to debug "
        "ETL reliability and improve data freshness."
    )


def test_extract_bullets_on_real_resume_certification_survives_nested_braces():
    # The Certifications section is \href{url}{\textbf{\textit{Name}}} --
    # two levels of nested braces inside \href's second argument. This is
    # the exact real-data shape that motivated
    # test_extract_bullets_handles_nested_braces_in_href above.
    out = bullets.extract_bullets(REAL_RESUME_TEX)
    cert_bullets = [b for b in out if b["section"] == "Certifications"]
    assert len(cert_bullets) == 3
    assert any("AWS Certified Solutions Architect" in b["text"] for b in cert_bullets)
    assert any("Issued Mar 2024" in b["text"] for b in cert_bullets)
