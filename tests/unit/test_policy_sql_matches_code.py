"""The policy SQL and actionable_preferences must agree on what is executable.

They drifted on 2026-10-01, the same day the shape they disagreed about was
added. `scripts/set_composition_policy_structured.sql` gained a bare
{"include": "Yuno Energy"} rule, and its own verification query still read

    WHERE r ? 'exclude' OR (r ? 'include' AND r ? 'over')

which cannot count that rule. It would have printed `executable_rules = 3`
directly beneath a comment promising 4 — a check that cannot see the thing it
checks, which is the defect class the file exists to close.

Nothing connected the two. The SQL is run by hand in the Supabase console and
no test had ever read it, so the only signal would have been the user noticing
a number that disagreed with a comment.
"""
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.composition_policy import actionable_preferences  # noqa: E402

SQL = (_ROOT / "scripts" / "set_composition_policy_structured.sql").read_text()


def _prefer_rules_from_sql() -> list[dict]:
    """The `prefer` array's rules, as the keys each jsonb_build_object sets."""
    block = SQL.split("'prefer', jsonb_build_array(", 1)[1].split("'emphasise'", 1)[0]
    rules = []
    for obj in block.split("jsonb_build_object(")[1:]:
        keys = re.findall(r"'(include|over|exclude|why)',", obj)
        # The value never matters here, only the shape; a non-empty placeholder
        # keeps actionable_preferences' truthiness checks honest.
        rules.append({k: f"<{k}>" for k in keys})
    return rules


def test_the_sql_defines_the_rules_the_user_asked_for():
    """Guards the parser: if this finds nothing the rest proves nothing."""
    rules = _prefer_rules_from_sql()
    assert len(rules) == 4, rules
    shapes = sorted(tuple(sorted(r)) for r in rules)
    assert shapes == sorted([
        ("include", "why"),                 # must be present  (Yuno Energy)
        ("include", "over", "why"),         # prefer A to B    (UTA IT / Kraken)
        ("exclude", "why"),                 # never include    (the TA role)
        ("include", "over", "why"),         # prefer A to B    (Purrrfect Keys / thesis)
    ]), shapes


def test_every_rule_in_the_sql_is_one_the_code_can_execute():
    """A rule the SQL stores but the code ignores is a rule that silently does
    nothing — the state `prefer` was in before it became executable at all."""
    rules = _prefer_rules_from_sql()
    actionable = actionable_preferences({"prefer": rules})
    assert len(actionable) == len(rules), (
        f"{len(rules) - len(actionable)} rule(s) in the SQL are not executable "
        f"by actionable_preferences: {[r for r in rules if r not in actionable]}"
    )


def test_the_sql_verification_query_counts_what_the_code_counts():
    """The console SELECT is the only feedback the operator gets. If its
    predicate is narrower than actionable_preferences, it under-reports and the
    operator is told a correct policy is wrong."""
    predicate = SQL.split("AS executable_rules")[0].rsplit("WHERE", 1)[1]
    assert "r ? 'exclude'" in predicate
    assert "r ? 'include'" in predicate
    # The bug: `AND r ? 'over'` narrows include to the two-sided shape only.
    assert "AND r ? 'over'" not in predicate, (
        "the verification query cannot count a bare {\"include\": ...} rule"
    )


def test_the_expected_count_in_the_comment_matches_the_rules():
    """The comment is the operator's expectation. It must track the file."""
    n = len(_prefer_rules_from_sql())
    words = {3: "THREE", 4: "FOUR", 5: "FIVE"}
    assert f"{words[n]} prefer rules" in SQL, (
        f"the SQL defines {n} rules but its Expect: comment says otherwise"
    )
