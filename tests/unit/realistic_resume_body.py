r"""A resume body with as much identity in it as a real one has.

Added when `check_near_empty` was wired into `check_output` for the `tailor`
task. Twenty-one existing tests failed, all of them on fixtures like

    \section*{Summary} Backend engineer.
    \section*{Technical Skills} Python, AWS, Docker
    \section*{Experience} \textbf{Engineer} built services.
    ... three more one-line sections

which is a ~250-character document with 3 identity anchors. Every real
tailored resume measured on 2026-09-30 -- 740 of them -- carries between 102
and 289, median 236, and the Technical Skills section of this user's corpus
carries 126 on its own. So the fixtures were not near-misses on the new
floor; they were two orders of magnitude away from any document the pipeline
has ever produced, and the tests built on them could not have noticed a guard
that reads document content rather than document structure.

They were fine for what they were written for -- section headers and brace
balance are structural, and a 250-char stub exercises those honestly. But
CLAUDE.md rule 6 is that a test double which fails the way the bug fails is
worse than no test, and a fixture that trips a content check for being a stub
is exactly that: it would have presented as "the near-empty check is too
aggressive" when what it actually showed is that no fixture in the suite
resembled a resume.

`SKILLS` below is abridged from the real 2026-09-28 corpus row rather than
invented, so the anchor density is the production density and not a number
chosen to clear a threshold. `assert_realistic()` pins that: it fails if a
change to `extract_anchors` (PR #167 changes it) moves this fixture's count
near the floor, so the suite reports the instrument moving instead of
silently losing sensitivity.
"""

# Abridged from the real corpus row's \section*{Technical Skills}. Six
# categories instead of nine, same shape, same kind of tokens.
SKILLS = (
    r"\begin{itemize}"
    r"\item \textbf{Languages \& Frameworks:} Python (FastAPI, SQLAlchemy, boto3, "
    r"pandas, Flask), TypeScript/JavaScript (React, Node.js/Express), SQL "
    r"(PostgreSQL, MySQL), Bash"
    r"\item \textbf{Containers \& Orchestration:} Docker, Kubernetes (EKS, k3s), "
    r"Helm, Kustomize, Traefik, ECR"
    r"\item \textbf{Cloud \& Serverless:} AWS (Lambda, Step Functions, API Gateway, "
    r"EventBridge, SQS/SNS, Aurora, DynamoDB, S3, CloudFront, Cognito, IAM, KMS, "
    r"Route53, Fargate), Firebase, Supabase"
    r"\item \textbf{IaC \& Automation:} Terraform, AWS SAM, CloudFormation, Ansible, "
    r"Alembic"
    r"\item \textbf{CI/CD:} GitHub Actions, Jenkins, CodePipeline, Bitbucket Pipelines"
    r"\item \textbf{Testing \& QA:} pytest, Jest, Vitest, Playwright, Maestro"
    # Kept as this exact literal because the fabrication tests substitute into
    # it ("Python, AWS" -> "Java, Python, AWS", "Docker" -> "Docker, Rust") and
    # check_fabrication's Skills-section regex only sees text between
    # \section*{...Skills} and the next \section*{.
    #
    # The newline is load-bearing. check_fabrication's `_claims` splits the
    # de-macro'd text on [,&/()\n]+, and `_plain` turns "\item \textbf{Primary:}
    # Python" into the single field "item Primary: Python" -- so the FIRST
    # entry of any \item is invisible to the blocklist unless a separator
    # precedes it. That is a real pre-existing gap in check_fabrication, not a
    # property of this fixture; it is recorded here so the newline is not
    # "tidied" away and the fabrication tests silently stop testing anything.
    "\n" r"\item \textbf{Primary:}" "\n" r"Python, AWS, Docker"
    r"\end{itemize}"
)

# The same tokens with no \begin{itemize} wrapper, for fixtures whose preamble
# defines only \jobentry/\projectentry and whose bodies are counted by
# _validate_macro_arities. Same anchor density, no extra LaTeX environments.
SKILLS_FLAT = (
    "Python, FastAPI, SQLAlchemy, boto3, pandas, Flask, TypeScript, React, "
    "Node.js, Express, SQL, PostgreSQL, MySQL, Bash, Docker, Kubernetes, EKS, "
    "k3s, Helm, Kustomize, Traefik, ECR, AWS, Lambda, Step Functions, "
    "API Gateway, EventBridge, SQS, SNS, Aurora, DynamoDB, S3, CloudFront, "
    "Cognito, IAM, KMS, Route53, Fargate, Firebase, Supabase, Terraform, "
    "CloudFormation, Ansible, Alembic, GitHub Actions, Jenkins, CodePipeline, "
    "Bitbucket, pytest, Jest, Vitest, Playwright, Maestro"
)

EXPERIENCE = (
    r"\jobentry{Clover IT Services}{Dublin}{2023 -- 2026}{Software Engineer}"
    r"\begin{itemize}"
    r"\item Designed and maintained \textbf{8 production microservices} across a "
    r"multi-region AWS platform; sustained 99.9\% uptime."
    r"\item Architected observability with Prometheus, Grafana, CloudWatch and "
    r"X-Ray; reduced MTTR by 35\%."
    r"\end{itemize}"
    r"\jobentry{Seattle Kraken}{Remote}{2022 -- 2023}{Data Engineer}"
    r"\begin{itemize}"
    r"\item Built ingestion pipelines in Python and Airflow over Snowflake; cut "
    r"nightly runtime by 42\%."
    r"\end{itemize}"
)

PROJECTS = (
    r"\projectentry{Purrrfect Match}{2025}{React Native, Expo, Firebase}"
    r"\begin{itemize}\item Shipped to TestFlight with 1,200+ installs.\end{itemize}"
    r"\projectentry{UTWorld}{2024}{Next.js, Netlify}"
    r"\begin{itemize}\item Static site with Lighthouse 98.\end{itemize}"
)


def body(*, summary: str = "", extra: str = "") -> str:
    r"""A six-section tailored body carrying production-like anchor density."""
    return (
        r"\begin{center}{\Large \textbf{Utkarsh Singh}}\\"
        r"Dublin, Ireland \textbar\ 254utkarsh@gmail.com\end{center}"
        r"\section*{Summary}"
        + (summary or (r"Software engineer with \textbf{3+ years} building "
                       r"payment and data platforms; \textbf{MSc Cloud Computing}, "
                       r"University of Texas at Arlington."))
        + r"\section*{Technical Skills}" + SKILLS
        + r"\section*{Experience}" + EXPERIENCE
        + r"\section*{Featured Projects}" + PROJECTS
        + r"\section*{Education}MSc Cloud Computing, University of Texas at "
          r"Arlington, 2022. BTech, VIT Vellore, 2020."
        + r"\section*{Certifications}AWS Solutions Architect -- Professional, 2025. "
          r"Certified Kubernetes Administrator, 2024."
        + extra
    )


BODY = body()


def assert_realistic(min_anchors: int = 80) -> int:
    """Fail loudly if this fixture stops resembling a real resume.

    The floor `check_near_empty` uses is 40. This fixture must sit clearly
    above it for the right reason -- content -- so that a test using it which
    fails on `near_empty` means the code under test lost content, never that
    the fixture drifted. Called from the tests that depend on that.
    """
    from shared.resume_verify import extract_anchors

    found = len(extract_anchors(BODY, is_latex=True))
    assert found >= min_anchors, (
        f"the realistic fixture now yields only {found} anchors (want >= "
        f"{min_anchors}); extract_anchors changed, or the fixture was trimmed. "
        f"check_near_empty's floor is 40 and every test that relies on this "
        f"body passing it is now measuring the fixture, not the code."
    )
    return found
