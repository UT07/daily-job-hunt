"""Three CI guards that failed silently, pinned so they cannot narrow again.

Every bug in here shares a shape the project keeps hitting: a control that looks
like it is protecting something, reports success, and protects nothing.

1. deploy.yml had no `concurrency` group. CloudFormation executes one change
   set at a time per stack, so two deploys started seconds apart race: the
   first executes, the second's change set goes OBSOLETE and the run fails.
   Measured 2026-09-29 — it happened twice in forty minutes, on PRs merged 5
   and 7 seconds apart:

       failure  14:14:37  db75a65  (#126)   <- ChangeSet OBSOLETE
       success  14:14:30  ab9e4a3  (#125)
       failure  13:34:46  3d0473d  (#124)   <- same
       success  13:34:41  1c655e1  (#123)

   Each time the LATER commit is the one that died, so main silently moved
   ahead of production and every "merged and deployed" claim after that was
   wrong.

2. The AI Eval Gate's path filter listed three agent packages and evals/, which
   left the scoring path itself outside the gate — score_batch.py,
   ai_helper.py, tailor_resume.py and post_score.py sit directly under
   lambdas/pipeline/, not in a watched subdirectory. Measured against baseline
   commit f0a3648: 16 commits had landed since the baseline was frozen, 2
   touched a watched path, and 4 changed scoring through an unwatched one.
   The next PR to touch a watched path is then charged for all of them.

3. Test steps that ended in `|| true`. The shell operator discards the exit
   status, so pytest's verdict never reached the runner and the step reported
   success on every run — including runs where the file it named did not exist.
   Two were found and removed:

       test.yml  Browser E2E        npx playwright test --config=<missing> || true
       test.yml  integration-tests  pytest tests/integration/ -m integration || true

   The first ran against a config that has never existed in this repo (#154);
   the second was the last one left, and unlike the four REPORT jobs it had no
   `continue-on-error`, so `|| true` was the only thing making it green.
"""
import importlib
import inspect
import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
DEPLOY = WORKFLOWS / "deploy.yml"
TEST_WF = WORKFLOWS / "test.yml"


# --- 1. deploys must serialise ----------------------------------------------

def test_deploy_declares_a_concurrency_group():
    wf = yaml.safe_load(DEPLOY.read_text())
    assert "concurrency" in wf, (
        "deploy.yml has no concurrency group — two merges seconds apart will "
        "race and the later one dies with ChangeSet OBSOLETE"
    )


def test_deploy_queues_rather_than_cancels():
    """cancel-in-progress would kill a CloudFormation update mid-flight.

    Waiting is strictly better: the queued run picks up the newer commit
    anyway, whereas a cancelled stack update can leave the stack in
    UPDATE_ROLLBACK_FAILED, which needs manual intervention.
    """
    wf = yaml.safe_load(DEPLOY.read_text())
    assert wf["concurrency"]["cancel-in-progress"] is False


# --- 2. the eval gate must see the code that moves scores -------------------

def _eval_filter_pattern() -> str:
    """The extended-regex the detect-ai-changes step greps changed files with."""
    m = re.search(r"grep -qE '(\^\([^']+\))' changed_files\.txt", CI.read_text())
    assert m, "could not find the ai_relevant grep in ci.yml — did the step change shape?"
    return m.group(1)


# Every module that changes WHAT THE MODEL IS ASKED, or WHAT IT IS ASKED ABOUT.
MUST_TRIGGER = [
    "lambdas/pipeline/agents/nodes.py",
    "lambdas/pipeline/guardrails/output_guards.py",
    "lambdas/pipeline/retrieval/bullets.py",
    "lambdas/pipeline/score_batch.py",
    "lambdas/pipeline/ai_helper.py",
    "lambdas/pipeline/tailor_resume.py",
    "lambdas/pipeline/post_score.py",
    "lambdas/pipeline/generate_cover_letter.py",
    "shared/work_auth.py",
    "shared/resume_format.py",
    "resume_parser.py",
    "evals/baseline.json",
]

# The gate calls live providers, so it must stay off the paths that cannot move
# a score. A filter that matches everything is as useless as one that matches
# too little — it just burns quota instead of missing regressions.
MUST_NOT_TRIGGER = [
    "web/src/pages/Settings.jsx",
    "docs/ROADMAP.md",
    "README.md",
    "app.py",
    "template.yaml",
    "lambdas/scrapers/scrape_indeed.py",
    "shared/scrape_budget.py",
]


def test_eval_gate_sees_every_scoring_module():
    pat = re.compile(_eval_filter_pattern())
    missed = [p for p in MUST_TRIGGER if not pat.match(p)]
    assert not missed, (
        "the AI Eval Gate cannot see these, so a change to them merges "
        f"unmeasured: {missed}"
    )


def test_eval_gate_ignores_paths_that_cannot_move_a_score():
    pat = re.compile(_eval_filter_pattern())
    spurious = [p for p in MUST_NOT_TRIGGER if pat.match(p)]
    assert not spurious, f"gate would burn live-provider quota on: {spurious}"


def test_the_four_modules_that_went_unmeasured_are_covered():
    """The specific regression this change exists to prevent.

    #116 rewrote 97 lines of score_batch.py and 52 of ai_helper.py; #120/#121
    changed how an uploaded resume is parsed and selected, which is what every
    scoring prompt is scored against. None of them ran the gate.
    """
    pat = re.compile(_eval_filter_pattern())
    for path in ("lambdas/pipeline/score_batch.py", "lambdas/pipeline/ai_helper.py",
                 "resume_parser.py", "shared/resume_format.py"):
        assert pat.match(path), f"{path} still invisible to the gate"


# --- 3. a test step must not discard its own exit status ---------------------

def _run_steps():
    """Every `run:` script in every workflow, with enough context to name it.

    Parsed as YAML rather than grepped, deliberately: test.yml documents the
    deleted Playwright step in a comment that contains the literal `|| true`,
    and a grep over the raw text would flag the tombstone instead of live code.
    """
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text())
        for job_name, job in (workflow.get("jobs") or {}).items():
            for i, step in enumerate(job.get("steps") or []):
                script = step.get("run")
                if script:
                    label = step.get("name") or step.get("uses") or f"step {i}"
                    yield path.name, job_name, label, script


# `|| true`, and the spellings that mean exactly the same thing to a shell.
# Anything that makes the runner's exit-status check unreachable belongs here;
# catching only the literal `|| true` would leave `|| :` as a one-character way
# to reintroduce the same defect.
_SWALLOWS_EXIT = re.compile(r"\|\|\s*(?:true|:|exit\s+0)\s*(?:#.*)?$")

# Commands whose exit status IS the test result.
_RUNS_TESTS = re.compile(
    r"\b(?:pytest|playwright\s+test|vitest|npm\s+(?:run\s+)?test|python\s+-m\s+unittest)\b",
)


def _logical_commands(script: str) -> list[str]:
    """One entry per shell command, with backslash continuations joined.

    This matters, and the first version of this guard got it wrong. The
    integration step now spans two physical lines:

        pytest tests/integration/ -v --tb=short \\
               --junitxml=test-results/integration-junit.xml

    so `pytest` and a re-added `|| true` would not share a line, and a per-line
    check would not see them together. Proven by mutation: appending `|| true`
    to the continuation line left the per-line version of
    test_no_test_command_swallows_its_exit_status green.
    """
    joined = re.sub(r"\\\s*\n\s*", " ", script)
    return [
        " ".join(line.split())
        for line in joined.splitlines()
        # A shell comment cannot swallow anything, and test.yml keeps the
        # deleted Playwright command in prose for the record.
        if line.strip() and not line.strip().startswith("#")
    ]


def _swallowing_lines(script: str) -> list[str]:
    return [cmd for cmd in _logical_commands(script) if _SWALLOWS_EXIT.search(cmd)]


# The one legitimate `|| true` in the repo, and why. `git add` fails when the
# path does not exist, and output/seen_jobs.json is written by the pipeline run
# that precedes this step — a run that scraped nothing writes no file. The next
# line (`git diff --staged --quiet || git commit`) is what decides whether there
# is anything to commit, so swallowing `git add`'s status loses no signal. It is
# not a test command and no test verdict passes through it.
ALLOWED_SWALLOWERS = {
    (
        "daily_job_hunt.yml",
        "git add output/seen_jobs.json || true",
    ): "output/seen_jobs.json is absent when the run scraped nothing",
}


def test_the_detector_reads_across_a_line_continuation():
    """Pins the join, because the per-line version of this guard missed the bug.

    Both real offenders were single-line commands, but the replacement step is
    wrapped, so the operator that would discard pytest's verdict sits on the
    second physical line. Without the join, the guard below cannot see that the
    swallowed command is a test command.
    """
    wrapped = (
        "pytest tests/integration/ -v --tb=short \\\n"
        "       --junitxml=test-results/integration-junit.xml || true\n"
    )
    swallowed = _swallowing_lines(wrapped)
    assert swallowed == [
        "pytest tests/integration/ -v --tb=short "
        "--junitxml=test-results/integration-junit.xml || true",
    ]
    assert _RUNS_TESTS.search(swallowed[0]), "the joined command must read as a test command"


def test_the_detector_ignores_a_shell_comment():
    assert _swallowing_lines("# we used to run: pytest foo || true\npytest foo\n") == []


def test_no_test_command_swallows_its_exit_status():
    """The defect itself: a step whose pytest verdict never reaches the runner."""
    offenders = [
        f"{wf}:{job}:{label}: {line}"
        for wf, job, label, script in _run_steps()
        for line in _swallowing_lines(script)
        if _RUNS_TESTS.search(line)
    ]
    assert not offenders, (
        "these steps run tests and then discard the result, so they report "
        f"success whatever the tests did: {offenders}"
    )


def test_every_swallowed_exit_status_in_ci_is_documented():
    """Inventory pin, and the proof this detector still detects anything.

    The `missing` half is what keeps the guard honest: if _SWALLOWS_EXIT ever
    stopped matching, `undocumented` would be empty and the test above would
    pass vacuously. Requiring the one known-present line to still be found means
    a broken detector fails here instead of going quiet.
    """
    found = {
        (wf, line)
        for wf, _job, _label, script in _run_steps()
        for line in _swallowing_lines(script)
    }
    undocumented = sorted(found - set(ALLOWED_SWALLOWERS))
    assert not undocumented, (
        "`|| true` (or an equivalent) hides this step's exit status; add it to "
        f"ALLOWED_SWALLOWERS with a reason, or stop swallowing: {undocumented}"
    )
    missing = sorted(set(ALLOWED_SWALLOWERS) - found)
    assert not missing, (
        "ALLOWED_SWALLOWERS lists lines that no longer exist, so this guard is "
        f"no longer detecting anything it claims to allow: {missing}"
    )


# --- the integration job specifically ---------------------------------------

INTEGRATION_TESTS_DIR = REPO_ROOT / "tests" / "integration"


def _integration_job() -> dict:
    return yaml.safe_load(TEST_WF.read_text())["jobs"]["integration-tests"]


def _integration_pytest_step() -> str:
    runs = [
        s["run"] for s in _integration_job()["steps"]
        if "pytest tests/integration/" in (s.get("run") or "")
    ]
    assert len(runs) == 1, f"expected exactly one integration pytest step, got {len(runs)}"
    return runs[0]


def test_the_integration_job_blocks_rather_than_reports():
    """`continue-on-error` is the honest way to be non-blocking; this job is not.

    The suite needs no credentials, touches no network, and runs in under a
    tenth of a second, so there is nothing for it to be lenient about.
    """
    assert _integration_job().get("continue-on-error") is not True


def test_the_integration_job_does_not_narrow_its_own_population():
    """`-m integration` deselected a test that never got the decorator.

    23 of 24 ran and no output said so. The directory is the population now
    (CLAUDE.md rule 7), so a forgotten marker cannot shrink the gate.
    """
    script = _integration_pytest_step()
    assert not re.search(r"(?:^|\s)-m\s", script), (
        "the integration step filters by marker again; a test that forgets the "
        f"decorator will be silently dropped: {script!r}"
    )


def test_the_integration_job_proves_it_ran():
    script = "\n".join(s.get("run") or "" for s in _integration_job()["steps"])
    assert "--junitxml" in script, "no junit report, so the floor cannot be checked"
    assert "assert_suite_ran.py" in script, (
        "no floor check: a wholesale-skipped suite exits 0 and would read as "
        "coverage (CLAUDE.md rule 2)"
    )


def _marked_integration(obj) -> bool:
    return any(getattr(m, "name", None) == "integration" for m in getattr(obj, "pytestmark", []))


def test_every_integration_test_carries_the_marker_it_claims():
    """The other half of the `-m integration` bug.

    CI no longer filters by marker, but pytest.ini declares it and anyone
    running `pytest tests/integration -m integration` locally gets a count they
    will believe. One test went without the decorator and 23 of 24 ran. A
    marker on the module or the class counts too.
    """
    unmarked = []
    for path in sorted(INTEGRATION_TESTS_DIR.glob("test_*.py")):
        module = importlib.import_module(f"tests.integration.{path.stem}")
        if _marked_integration(module):
            continue
        for name, obj in vars(module).items():
            if name.startswith("test_") and inspect.isfunction(obj):
                if not _marked_integration(obj):
                    unmarked.append(f"{path.name}::{name}")
            elif inspect.isclass(obj) and name.startswith("Test") and not _marked_integration(obj):
                unmarked += [
                    f"{path.name}::{name}::{attr}"
                    for attr, fn in vars(obj).items()
                    if attr.startswith("test_") and inspect.isfunction(fn)
                    and not _marked_integration(fn)
                ]
    assert not unmarked, (
        "these integration tests have no `integration` marker, so `-m "
        f"integration` silently skips them: {unmarked}"
    )


def test_the_integration_floor_is_neither_vacuous_nor_unsatisfiable():
    """A floor of 0 proves nothing; a floor above the test count blocks everything."""
    script = "\n".join(s.get("run") or "" for s in _integration_job()["steps"])
    m = re.search(r"assert_suite_ran\.py\s+\S+\s+(\d+)", script)
    assert m, f"could not read the floor out of the integration job: {script!r}"
    floor = int(m.group(1))
    defined = sum(
        len(re.findall(r"^\s*def test_", path.read_text(), re.M))
        for path in INTEGRATION_TESTS_DIR.glob("test_*.py")
    )
    assert floor > 0, "a floor of 0 passes on a suite that ran nothing"
    assert floor <= defined, (
        f"floor is {floor} but only {defined} integration tests are defined, so "
        "the gate can never be satisfied"
    )
