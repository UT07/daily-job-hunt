"""Two CI guards that failed silently, pinned so they cannot narrow again.

Both bugs in here share a shape the project keeps hitting: a control that looks
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
"""
import pathlib
import re

import yaml

CI = pathlib.Path(".github/workflows/ci.yml")
DEPLOY = pathlib.Path(".github/workflows/deploy.yml")


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
