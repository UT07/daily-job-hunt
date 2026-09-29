"""A model that ran out of output budget must not look like one that finished.

The AI Eval Gate's `guard_pass_rate` fell 1.00 -> 0.92 on CI run 36481897936
(commit f0a3648). Two of the five golden tailor cases came back missing whole
sections:

    2x  required_sections:block: missing section: experience
    2x  required_sections:block: missing section: education
    2x  required_sections:block: missing section: projects
    2x  required_sections:block: missing section: certifications
    1x  required_sections:block: missing section: skills

Both failing cases lost a CONTIGUOUS TAIL of sections, in the order the
tailoring prompt asks for them (Summary, Technical Skills, Experience,
Featured Projects, Education, Certifications), and one stopped one section
earlier than the other. That is the shape of output that stopped early, not
of a model that ignored an instruction.

Two defects made that possible and kept it invisible, and this module pins
both:

1. The council generated with a hardcoded max_tokens=4096 regardless of how
   much output the task asked for. A tailoring prompt ends with "Return ONLY
   the tailored body" against a base body of 11,000-17,000 characters of
   LaTeX. Measured on resumes/fullstack.tex: 11,643 characters need at least
   2,430 tokens to emit (cl100k/o200k pre-tokenizer split, which BPE merges
   never cross, so that is a floor rather than an estimate) -- and the model
   registry records min_output_tokens: 3000 for the two gpt-oss entries
   because reasoning models spend the budget before they emit anything.

2. `_call_provider` consulted finish_reason ONLY when the content was empty.
   A response cut off mid-document came back as a normal candidate. Nothing
   downstream -- the critic, the guards, the eval report -- could tell it
   from a complete one, which is why the CI report could show four missing
   sections with nothing at all to say about why.
"""
import logging
import re
from unittest.mock import MagicMock, patch

import httpx
import pytest

import ai_helper
from agents import nodes
from agents.graph import _fan_out
from guardrails.output_guards import check_output

PROVIDER = {
    "name": "groq/gpt-oss-120b",
    "url": "https://api.groq.com/openai/v1/chat/completions",
    "key_param": "/naukribaba/GROQ_API_KEY",
    "model": "openai/gpt-oss-120b",
    "timeout": 90,
}


def _response(content, finish_reason="stop", reasoning=None):
    message = {"content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": message, "finish_reason": finish_reason}]
    }
    return resp


class TestCallProviderReportsTruncation:
    def test_length_finish_reason_on_non_empty_content_is_flagged(self):
        with patch("ai_helper.get_param", return_value="real-key"), \
             patch("httpx.post", return_value=_response("half a resu", "length")):
            result = ai_helper._call_provider(PROVIDER, "p", "s", 0.3, max_tokens=4096)

        assert result["content"] == "half a resu"
        assert result["truncated"] is True
        assert result["finish_reason"] == "length"

    def test_truncation_is_logged_with_the_budget_that_caused_it(self, caplog):
        with caplog.at_level(logging.WARNING), \
             patch("ai_helper.get_param", return_value="real-key"), \
             patch("httpx.post", return_value=_response("half a resu", "length")):
            ai_helper._call_provider(PROVIDER, "p", "s", 0.3, max_tokens=4096)

        # An engineer reading CloudWatch has to be able to tell "the model
        # ignored the instructions" from "the model never got to finish".
        assert "max_tokens=4096" in caplog.text
        assert "groq/gpt-oss-120b" in caplog.text

    def test_a_completed_response_is_not_flagged(self):
        with patch("ai_helper.get_param", return_value="real-key"), \
             patch("httpx.post", return_value=_response("a whole resume", "stop")):
            result = ai_helper._call_provider(PROVIDER, "p", "s", 0.3, max_tokens=4096)

        assert result["truncated"] is False
        assert result["finish_reason"] == "stop"

    def test_empty_content_still_returns_none(self):
        # Unchanged behaviour: nothing usable came back, so the caller fails
        # over rather than being handed an empty string to validate.
        with patch("ai_helper.get_param", return_value="real-key"), \
             patch("httpx.post", return_value=_response("", "length", reasoning="thinking...")):
            assert ai_helper._call_provider(PROVIDER, "p", "s", 0.3, max_tokens=60) is None


class TestTheCacheDoesNotPersistAFragment:
    """`ai_cache` keys on md5(system|prompt) with a 72h TTL. Storing a cut-off
    answer turns one provider hiccup into three days of identical failures
    for every caller that sends the same prompt, and no re-run shakes it off.
    """

    @staticmethod
    def _db():
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value \
            .gte.return_value.execute.return_value.data = []   # cache miss
        return db

    def test_a_truncated_response_is_returned_but_not_written(self):
        db = self._db()
        result = {"content": "half", "provider": "p", "model": "m", "truncated": True}
        with patch.object(ai_helper, "get_supabase", return_value=db), \
             patch.object(ai_helper, "ai_complete", return_value=result):
            assert ai_helper.ai_complete_cached("prompt")["content"] == "half"
        db.table.return_value.upsert.assert_not_called()

    def test_a_complete_response_is_still_cached(self):
        db = self._db()
        result = {"content": "whole", "provider": "p", "model": "m", "truncated": False}
        with patch.object(ai_helper, "get_supabase", return_value=db), \
             patch.object(ai_helper, "ai_complete", return_value=result):
            ai_helper.ai_complete_cached("prompt")
        db.table.return_value.upsert.assert_called_once()


class TestRewriteBudget:
    def test_scales_with_the_document_the_model_must_re_emit(self):
        small = ai_helper.rewrite_budget("x" * 3_000)
        large = ai_helper.rewrite_budget("x" * 30_000)
        assert large > small

    def test_never_returns_less_than_the_historical_default(self):
        # This function can only widen a budget. A caller that switches to it
        # must never end up with less room than the 4096 it had before.
        assert ai_helper.rewrite_budget("") == 4096
        assert ai_helper.rewrite_budget("x" * 100) == 4096

    def test_caps_at_the_pool_wide_ceiling(self):
        assert ai_helper.rewrite_budget("x" * 10_000_000) == ai_helper.MAX_OUTPUT_TOKENS_CAP

    def test_leaves_room_to_write_after_a_reasoning_model_has_thought(self):
        # The registry's min_output_tokens floor for the gpt-oss entries is
        # 3000: that much of the budget can be gone before the first visible
        # character. What is left still has to hold the document.
        body = "x" * 11_643  # resumes/fullstack.tex body, measured
        budget = ai_helper.rewrite_budget(body)
        assert budget - 3000 >= len(body) / ai_helper._CHARS_PER_TOKEN_LATEX

    def test_a_real_resume_body_no_longer_fits_in_the_old_4096_default(self):
        # The regression in one line: this is the call the tailor path makes,
        # and 4096 is what it used to get.
        assert ai_helper.rewrite_budget("x" * 11_643) > 4096


class TestPreferComplete:
    COMPLETE = {"content": "whole", "provider": "a", "model": "m1", "truncated": False}
    CUT_OFF = {"content": "part", "provider": "b", "model": "m2", "truncated": True}

    def test_drops_truncated_candidates_when_a_complete_one_exists(self):
        assert ai_helper.prefer_complete([self.CUT_OFF, self.COMPLETE]) == [self.COMPLETE]

    def test_keeps_everything_when_every_candidate_was_cut_off(self):
        # A partial answer still beats no answer; the caller's own validation
        # decides whether to ship it.
        both = [self.CUT_OFF, dict(self.CUT_OFF, provider="c")]
        assert ai_helper.prefer_complete(both) == both

    def test_candidates_without_the_key_are_treated_as_complete(self):
        # Checkpointed state and older tests build candidates with only
        # content/provider/model.
        legacy = [{"content": "whole", "provider": "a", "model": "m1"}]
        assert ai_helper.prefer_complete(legacy) == legacy


class TestBudgetReachesTheProvider:
    """A parameter that is accepted and then dropped is worse than no
    parameter: it reads as a fix in review and changes nothing at runtime.
    """

    def test_legacy_council_forwards_max_tokens_to_its_generators(self):
        seen = []

        def record(provider, prompt, system, temperature, max_tokens=4096):
            seen.append(max_tokens)
            return {"content": "c", "provider": provider["name"],
                    "model": provider["model"], "truncated": False}

        with patch.object(ai_helper, "_call_provider", side_effect=record):
            ai_helper._council_complete_legacy("p", "s", n_generators=2, max_tokens=7777)

        assert seen, "no provider call was made"
        # The critic is sized separately (CRITIC_MAX_TOKENS) — generators are
        # what carry the document.
        assert 7777 in seen

    def test_council_complete_defaults_to_the_historical_4096(self):
        seen = []

        def record(provider, prompt, system, temperature, max_tokens=4096):
            seen.append(max_tokens)
            return {"content": "c", "provider": provider["name"],
                    "model": provider["model"], "truncated": False}

        with patch.object(ai_helper, "_call_provider", side_effect=record):
            ai_helper.council_complete("p", "s", n_generators=1)

        assert seen == [4096]

    def test_generate_node_uses_the_budget_in_its_payload(self):
        with patch.object(nodes, "call_one", return_value={"content": "c"}) as call_one:
            nodes.generate_node({
                "provider": PROVIDER, "prompt": "p", "system": "",
                "temperature": 0.3, "max_tokens": 7777,
            })
        assert call_one.call_args.kwargs["max_tokens"] == 7777

    def test_generate_node_falls_back_to_4096_without_one(self):
        with patch.object(nodes, "call_one", return_value={"content": "c"}) as call_one:
            nodes.generate_node({"provider": PROVIDER, "prompt": "p", "system": "", "temperature": 0.3})
        assert call_one.call_args.kwargs["max_tokens"] == 4096

    def test_fan_out_carries_max_tokens_into_every_generate_branch(self):
        sends = _fan_out({
            "prompt": "p", "system": "", "temperature": 0.3, "max_tokens": 7777,
            "generators": [PROVIDER, dict(PROVIDER, name="other")],
        })
        assert [s.arg["max_tokens"] for s in sends] == [7777, 7777]


class TestCritiqueNodePrefersCompleteCandidates:
    def test_a_cut_off_candidate_cannot_win_against_a_complete_one(self):
        # The critic scores prose; it cannot see that an answer stops
        # mid-section. With one complete candidate available, the truncated
        # one must not even reach it.
        complete = {"content": "whole", "provider": "a", "model": "openai/gpt-oss-120b",
                    "truncated": False}
        cut_off = {"content": "part", "provider": "b", "model": "z-ai/glm-5.2:free",
                   "truncated": True}
        with patch.object(nodes, "call_one") as call_one:
            out = nodes.critique_node({"candidates": [cut_off, complete]})
        assert out["winner"] == complete
        call_one.assert_not_called()  # one candidate left — no critique needed


class TestTruncationProducesExactlyTheReportedViolations:
    """The fingerprint from CI run 36481897936, reproduced without a provider.

    resumes/fullstack.tex stands in for the user's base resume: same six
    sections, same order, same shape.
    """

    @staticmethod
    def _base_body():
        import pathlib
        tex = (pathlib.Path(__file__).resolve().parents[2] / "resumes/fullstack.tex").read_text()
        start = tex.find(r"\begin{document}") + len(r"\begin{document}")
        return tex[start:tex.rfind(r"\end{document}")].strip()

    def _guard(self, content):
        body = self._base_body()
        skills = re.search(r"\\section\*\{(?:Technical )?Skills\}(.*?)\\section\*\{", body, re.DOTALL)
        return check_output(content, "tailor",
                            base_skills_text=skills.group(1) if skills else "",
                            base_body=body)

    @staticmethod
    def _missing(guard):
        return [v.detail.removeprefix("missing section: ")
                for v in guard.violations if v.rule == "required_sections"]

    def test_a_complete_body_passes_every_block_level_guard(self):
        guard = self._guard(self._base_body())
        assert self._missing(guard) == []
        assert guard.passed

    def test_cut_off_after_skills_loses_exactly_the_four_trailing_sections(self):
        guard = self._guard(self._base_body()[:2600])
        assert self._missing(guard) == ["experience", "education", "projects", "certifications"]
        assert guard.passed is False

    def test_cut_off_before_skills_loses_all_five(self):
        guard = self._guard(self._base_body()[:900])
        assert self._missing(guard) == [
            "experience", "skills", "education", "projects", "certifications",
        ]
        assert guard.passed is False

    def test_the_two_cuts_sum_to_the_counts_the_ci_report_recorded(self):
        counts = {}
        for cut in (2600, 900):
            for section in self._missing(self._guard(self._base_body()[:cut])):
                counts[section] = counts.get(section, 0) + 1
        assert counts == {
            "experience": 2, "education": 2, "projects": 2,
            "certifications": 2, "skills": 1,
        }


P1 = {"name": "groq/a", "model": "openai/gpt-oss-120b", "key_param": "/k/a", "url": "u"}
P2 = {"name": "or/b", "model": "z-ai/glm-5.2:free", "key_param": "/k/b", "url": "u"}


class TestEndToEndThroughTheEvalHarness:
    """The whole path, with a fake provider that actually obeys max_tokens.

    This is the reproduction that could not be run against live providers:
    the golden set needs Groq/Gemini/Supabase credentials. Instead the HTTP
    layer is replaced by a model that behaves the way a reasoning model does
    — it spends `_REASONING_HEADROOM_TOKENS` of the budget thinking, writes
    with what is left, and reports finish_reason "length" if it runs out.

    What this pins is the WIRING, which is what was broken: that
    `_run_tailor_case` computes a budget at all, that the budget reaches the
    provider's request body, that a cut-off response is recognised as one,
    and that the guard verdict flips between the two budgets. The fake's
    emission rate is `ai_helper._CHARS_PER_TOKEN_LATEX` on purpose — the
    point is the relationship between budget and document size, not a
    particular token count.
    """

    URL = "https://fake.test/v1/chat/completions"
    POOL = [
        {"name": "fake/a", "model": "openai/gpt-oss-120b", "key_param": "/k/a",
         "url": URL, "timeout": 30},
        {"name": "fake/b", "model": "z-ai/glm-5.2:free", "key_param": "/k/b",
         "url": URL, "timeout": 30},
    ]

    @staticmethod
    def _resume_tex():
        import pathlib
        return (pathlib.Path(__file__).resolve().parents[2] / "resumes/fullstack.tex").read_text()

    def _fake_post(self, body):
        """A provider that writes until the budget runs out, then stops."""
        def post(url, headers=None, json=None, timeout=None):
            writable = int(
                (json["max_tokens"] - ai_helper._REASONING_HEADROOM_TOKENS)
                * ai_helper._CHARS_PER_TOKEN_LATEX
            )
            cut = max(writable, 0)
            return _response(body[:cut], "length" if cut < len(body) else "stop")
        return post

    def _run_case(self, old_budget=False):
        from evals import harness

        resume_tex = self._resume_tex()
        _, body = harness._split_tex(resume_tex)
        case = {
            "id": "254a4d0d9cf0", "task": "tailor",
            "title": "AWS Devops Engineer", "company": "DB Recruitment",
            "description": "DevOps engineering opportunity in Dublin.",
            "expected": {"must_contain": ["AWS"], "must_pass_guards": True,
                         "no_fabrication": True},
        }
        # The harness imports from `lambdas.pipeline.ai_helper`; this module
        # imports the flat `ai_helper`. tests/conftest.py puts BOTH spellings
        # on sys.path, so those are two distinct module objects and a patch
        # on one does not reach the other — patch the one the harness holds.
        from lambdas.pipeline import ai_helper as pipeline_ai_helper

        # Restoring the pre-fix world means restoring what the harness ASKS
        # for, not overriding what the fake provider honours — the whole
        # defect was in the size of the request.
        budget = patch.object(harness, "rewrite_budget", return_value=4096) \
            if old_budget else patch.object(harness, "rewrite_budget",
                                            wraps=harness.rewrite_budget)
        with patch.object(pipeline_ai_helper, "get_param", return_value="real-key"), \
             patch.object(pipeline_ai_helper, "_build_provider_list", return_value=list(self.POOL)), \
             patch("httpx.post", side_effect=self._fake_post(body)), budget:
            return harness._run_tailor_case(case, resume_tex=resume_tex)

    @staticmethod
    def _missing_sections(result):
        return [v.split("missing section: ")[1] for v in result["violations"]
                if v.startswith("required_sections")]

    # Order the tailoring prompt asks for them in, minus Summary (which the
    # required-sections guard does not check).
    EMISSION_ORDER = ["skills", "experience", "projects", "education", "certifications"]

    def test_at_the_old_hardcoded_4096_the_case_fails_the_way_ci_reported(self):
        result = self._run_case(old_budget=True)

        assert result["guards_passed"] is False
        assert result["truncated"] is True
        assert result["body_chars"] < result["base_body_chars"]

        missing = self._missing_sections(result)
        # The CI fingerprint is not "some sections went astray" — it is a
        # CONTIGUOUS TAIL of them, in the order the prompt asks for them.
        # Exactly where the cut lands depends on the resume; that it lands
        # somewhere and takes everything after it is the signature.
        assert missing, "expected the truncated body to lose sections"
        assert set(missing) == set(self.EMISSION_ORDER[-len(missing):])
        assert "certifications" in missing

    def test_at_the_budget_the_document_needs_the_same_case_passes(self):
        result = self._run_case()

        assert result["truncated"] is False
        assert result["body_chars"] == result["base_body_chars"]
        assert self._missing_sections(result) == []
        assert result["guards_passed"] is True


@pytest.mark.parametrize("engine", ["legacy", "langgraph"])
def test_both_council_engines_forward_the_budget_to_their_generators(engine, monkeypatch):
    """Legacy is the engine that actually runs today: template.yaml's
    CouncilEngine parameter defaults to "legacy" and the CI ai-eval job sets
    no COUNCIL_ENGINE at all. The 2026-09-28 run confirms it — its log
    carries legacy's own "Critic call failed, returning first candidate"
    wording, comma and all, not the graph's em-dash variant. Fixing only the
    LangGraph path would have fixed nothing that CI measures, so pin both.

    `agents/` reaches the provider layer through the agents.providers seam,
    not through ai_helper directly, so each engine needs its own patch target
    (same split tests/contract/test_council_engine_parity.py uses).
    """
    monkeypatch.setenv("COUNCIL_ENGINE", engine)
    seen = []

    def record(provider, prompt, system="", temperature=0.3, max_tokens=4096):
        seen.append(max_tokens)
        return {"content": "c", "provider": provider["name"],
                "model": provider["model"], "truncated": False}

    seam = "agents.providers" if engine == "langgraph" else "ai_helper"
    with patch(f"{seam}._call_provider", side_effect=record), \
         patch(f"{seam}._build_provider_list", return_value=[P1, P2]):
        ai_helper.council_complete("p", "s", n_generators=1, max_tokens=7777)

    # Only the generator call is sized by this; the critic keeps
    # CRITIC_MAX_TOKENS, and with one generator there is no critique round.
    assert seen == [7777]
