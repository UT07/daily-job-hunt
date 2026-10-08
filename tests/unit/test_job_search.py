"""One search box over title and company, and the term it is given is hostile.

The dashboard already had `company` and `title` filters, which are AND-ed
narrowing controls: to find a job you had to know which column the word lived
in. With a few hundred jobs the first thing anyone does is look for one, so `q`
searches both.

The security half is why this file exists rather than a one-line change. The
term reaches PostgREST's `or=` expression language, where the punctuation IS
the syntax:

    ,   ends one condition and starts the next
    .   separates column.operator.value
    ()  groups conditions
    *%  wildcards
    \\   escapes

so an uncleaned term does not merely break the search, it rewrites the query.
`q=x,user_id.neq.<someone-else>` is a cross-tenant read. The term is therefore
WHITELISTED, not escaped: the characters a real company or job title needs are
a short knowable list, and an escape table that misses one fails open.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_client import SupabaseClient  # noqa: E402

clean = SupabaseClient._safe_search_term


class TestRealSearchesSurvive:
    """A sanitiser nobody can search with gets removed, so this half matters."""

    @pytest.mark.parametrize("term", [
        "stripe", "Senior SRE", "AT&T", "Jones & Co", "CI/CD", "C++", "C#",
        "O'Reilly", "site-reliability",
    ])
    def test_a_name_a_person_would_type_is_kept(self, term):
        assert clean(term) == term


class TestTheQueryCannotBeRewritten:
    """Each of these is the punctuation that gives `or=` its structure."""

    def test_a_comma_cannot_start_a_second_condition(self):
        """The cross-tenant one: `or=(title.ilike.%x%,user_id.neq.me)` would
        return rows belonging to someone else."""
        out = clean("x,user_id.neq.00000000-0000-0000-0000-000000000000")
        assert "," not in out
        assert "." not in out

    def test_a_dot_cannot_separate_column_operator_value(self):
        assert "." not in clean("a.b.c")

    def test_parentheses_cannot_group_conditions(self):
        assert "(" not in clean("x)or(y") and ")" not in clean("x)or(y")

    def test_wildcards_cannot_be_injected(self):
        out = clean("%*%")
        assert "%" not in out and "*" not in out

    def test_a_backslash_cannot_escape_out(self):
        assert "\\" not in clean("x\\,y")

    def test_quotes_are_dropped(self):
        out = clean('x"y\'z')
        assert '"' not in out
        # the apostrophe is kept on purpose -- O'Reilly -- and is inert here
        # because it is not punctuation in PostgREST's filter grammar
        assert "'" in out


class TestShape:
    def test_runs_of_whitespace_collapse(self):
        assert clean("  spaced   out  ") == "spaced out"

    def test_an_empty_or_punctuation_only_term_is_empty(self):
        """Falsy, so the caller skips the filter entirely rather than searching
        for `%%` and returning the whole table as if it were a result."""
        assert clean("") == ""
        assert clean("%*()\\") == ""
        assert clean(None) == ""

    def test_the_term_is_bounded(self):
        assert len(clean("x" * 5000)) <= 80


def test_the_filter_is_skipped_when_the_term_cleans_to_nothing():
    """`if term:` in get_jobs, asserted through its consequence: a term of pure
    punctuation must not become `title.ilike.%%`, which matches everything and
    would read as "search is broken" rather than "nothing matched"."""
    assert not clean(",,,...")
