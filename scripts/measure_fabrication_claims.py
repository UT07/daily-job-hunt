#!/usr/bin/env python3
r"""False-positive rate of the fabrication claim-extractor, over real output.

Why this script exists
----------------------
`guardrails.output_guards.check_fabrication` is a BLOCKING check: a violation
routes the council to `repair_node` and costs up to two extra LLM rounds
(`agents/nodes.py::quality_gate`). So widening what it can see is not free, and
CLAUDE.md rule 16 says the false-positive rate of such a widening is measured
over the real population BEFORE it ships, and stated with its denominator.

This script is that measurement, kept in the repo rather than run once and
summarised in a commit message, because both sides of it keep moving: the
blocklist, `_claims`, and the corpus all change. Re-run it whenever any of
them does.

What it measures
----------------
Four arms, because a comparison has two sides and CLAUDE.md rule 15 is that
you vary the baseline, not just the rule:

    rule \ baseline     union of every user_resumes row     newest row only
    ----------------------------------------------------------------------
    frozen (pre-fix)    (a)                                 (c)
    live   (shipping)   (b)                                 (d)

(b) is the number the gate is about. (a) is what production did before.
(c)/(d) exist because the single-row baseline is the trap rule 15 records:
against the degraded 2026-09-28 row alone, word-boundary matching flagged
97 of 140 resumes versus 23 against the union, and all 74 differences were
Java -- a skill the candidate genuinely has, listed in the 2026-04-05 row.
A fix that looks expensive may only be measuring the wrong baseline, so both
are always printed together.

The frozen arm is a verbatim copy of the pre-fix `_claims`/`_plain`, pinned in
this file (see `_FROZEN_*` below) so the two rules can still be run against
each other after the fix has landed in output_guards.py.

Alongside the rates it prints every document the live rule flags and the
frozen rule did not, with the token and region -- that list is what gets
hand-adjudicated, because a rate on its own cannot tell a caught fabrication
from a false positive.

Corpus sources
--------------
    --source loader   output/_qa/triple_loader.load_triples(n,
                      exclude_untailored=True) -- the real tailored corpus.
                      This is the gate. `exclude_untailored` is not optional:
                      222 of the 929 stored files are the April base resume
                      copied verbatim, and scoring a document against itself
                      would report a 0% rate for the wrong reason (rule 7 --
                      scope a check to the population it is meant to judge).
                      The loader is operator-local and gitignored; it is not
                      part of this repo.
    --source dir      every *.tex under --tex-dir.
    --source git      every tailored .tex recoverable from this repo's git
                      history (output/**/*.tex, added under commits 9f14558 /
                      e55fd4f). Four distinct documents -- enough to prove the
                      harness runs and to exercise the real Skills-section
                      shape, NOWHERE NEAR enough for the rule 16 gate. The
                      output says so.

Baseline sources
----------------
    --baseline-tex PATH...   local .tex files, joined -- mirrors
                             shared.resume_format.BaseResume.all_tex.
    (default)                SUPABASE_URL + SUPABASE_KEY, read with httpx.get.

Production access here is READ-ONLY BY CONSTRUCTION: this script only ever
issues httpx.get. It must stay that way -- it has no reason to write, and a
measurement harness that can mutate the corpus it measures is one bad flag
away from destroying its own baseline.

Usage:
    source .venv/bin/activate
    python scripts/measure_fabrication_claims.py --source loader --n 740
    python scripts/measure_fabrication_claims.py --source git \
        --baseline-tex resumes/fullstack.tex resumes/sre_devops.tex
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas" / "pipeline"))

from guardrails import output_guards as og  # noqa: E402

# --- the pre-fix rule, frozen ------------------------------------------------
# Verbatim from output_guards.py before this change. Kept so the two rules can
# be compared after the fix lands; do not "modernise" it to match the live one,
# which would silently make every arm identical and every delta empty -- a
# no-op run that reports success (CLAUDE.md rule 2).


def _FROZEN_plain(fragment: str) -> str:
    out = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", fragment)
    return re.sub(r"[{}\\]", "", out)


def _FROZEN_claims(text: str) -> set[str]:
    return {
        item.strip().lower() for item in re.split(r"[,&/()\n]+", text)
    } & og._KNOWN_FABRICATIONS


def _FROZEN_regions(tex: str) -> list[tuple[str, str]]:
    regions = []
    header = og._HEADER_SUBTITLE_RE.search(tex)
    if header:
        regions.append(("header subtitle", _FROZEN_plain(header.group(1))))
    skills = og._SKILLS_SECTION_RE.search(tex)
    if skills:
        regions.append(("Skills section", _FROZEN_plain(skills.group(1))))
    return regions


RULES = {
    "frozen (pre-fix)": (_FROZEN_regions, _FROZEN_claims),
    "live (shipping)": (og._fabrication_regions, og._claims),
}

# The token _self_check plants. Must be a blocklist entry this candidate has
# never listed in any row, or the check tests nothing (the plant would be
# exonerated by the baseline and neither arm would flag it).
_PLANT = "Kotlin"


def violations(tex: str, base_lower: str, rule: str) -> list[tuple[str, str]]:
    """(skill, region) pairs one rule reports for one document.

    Mirrors check_fabrication's own loop, including its dedupe across regions,
    rather than calling it -- the frozen arm needs the same loop around a
    different extractor, and two loops would be two things to keep in step.
    """
    regions, claims = RULES[rule]
    out, seen = [], set()
    for region, text in regions(tex):
        for skill in sorted(claims(text)):
            if skill in seen or og._supported_by_base(skill, base_lower):
                continue
            seen.add(skill)
            out.append((skill, region))
    return out


# The entry the frozen rule could not see: whatever follows `\item`, past an
# optional `\textbf{Category:}` label, up to the first separator it DID split
# on. Read off the RAW region, not the `_plain`ed one, because `_plain` is part
# of what changed -- measuring the exposure with the new `_plain` would fold the
# fix into its own baseline.
_LEADING_ENTRY_RE = re.compile(
    r"\\item\s*(?:\\textbf\{[^}]*\})?\s*([^,&/()\n;:]+)"
)


def _leading_entries(tex: str) -> list[str]:
    """First entry of every `\\item` inside the regions this guard inspects."""
    out = []
    for pattern in (og._HEADER_SUBTITLE_RE, og._SKILLS_SECTION_RE):
        match = pattern.search(tex)
        if match:
            out += _LEADING_ENTRY_RE.findall(match.group(1))
    return out


# --- corpus ------------------------------------------------------------------


def _tex_of(triple, index: int) -> str:
    """The tailored .tex out of whatever shape the loader yields."""
    if isinstance(triple, str):
        return triple
    for key in ("tex", "tex_content", "tailored_tex", "resume_tex", "content"):
        if isinstance(triple, dict) and isinstance(triple.get(key), str):
            return triple[key]
        value = getattr(triple, key, None)
        if isinstance(value, str):
            return value
    if isinstance(triple, (list, tuple)):
        for item in triple:
            if isinstance(item, str) and "\\section" in item:
                return item
    raise SystemExit(
        f"triple {index} has shape {type(triple).__name__} "
        f"({sorted(triple) if isinstance(triple, dict) else dir(triple)[:20]}) "
        "and no field this script recognises as the tailored .tex. Teach "
        "_tex_of() the field name rather than guessing -- picking the wrong "
        "string here measures the wrong document and still prints a rate."
    )


def load_corpus(args) -> tuple[list[tuple[str, str]], str]:
    """[(label, tex)], plus a one-line provenance note for the report."""
    if args.source == "loader":
        sys.path.insert(0, str(ROOT / "output" / "_qa"))
        try:
            from triple_loader import load_triples  # type: ignore
        except ImportError as exc:
            raise SystemExit(
                f"--source loader needs output/_qa/triple_loader.py: {exc}\n"
                "It is operator-local and gitignored. Without it this script "
                "cannot reach the real corpus, and no other source satisfies "
                "the rule 16 gate."
            ) from exc
        triples = load_triples(args.n, exclude_untailored=True)
        return (
            [(f"triple[{i}]", _tex_of(t, i)) for i, t in enumerate(triples)],
            f"output/_qa/triple_loader.load_triples({args.n}, "
            "exclude_untailored=True)",
        )

    if args.source == "dir":
        paths = sorted(Path(args.tex_dir).rglob("*.tex"))
        return (
            [(p.name, p.read_text(errors="replace")) for p in paths],
            f"{len(paths)} *.tex under {args.tex_dir}",
        )

    # git: every tailored .tex this repo ever committed under output/.
    listing = subprocess.run(
        ["git", "log", "--all", "--pretty=format:", "--name-only",
         "--", "output/**/*.tex"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    docs, seen = [], set()
    for path in sorted(set(listing)):
        commits = subprocess.run(
            ["git", "log", "--all", "--pretty=format:%H", "--", path],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
        for commit in commits:
            blob = subprocess.run(
                ["git", "show", f"{commit}:{path}"],
                cwd=ROOT, capture_output=True, text=True,
            )
            tex = blob.stdout
            if blob.returncode or not tex.strip() or tex in seen:
                continue
            seen.add(tex)
            docs.append((f"{commit[:7]}:{Path(path).name}", tex))
    return docs, f"{len(docs)} distinct tailored .tex from git history"


def load_baselines(args) -> tuple[str, str, str]:
    """(union_of_all_rows, newest_row_only, provenance)."""
    if args.baseline_tex:
        rows = [Path(p).read_text(errors="replace") for p in args.baseline_tex]
        note = f"{len(rows)} local .tex: {', '.join(args.baseline_tex)}"
    else:
        url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
        if not (url and key):
            raise SystemExit(
                "no baseline: set SUPABASE_URL + SUPABASE_KEY, or pass "
                "--baseline-tex. An empty baseline makes every claim "
                "unsupported and every arm report ~100% -- a number that "
                "looks like a catastrophic regression and means nothing."
            )
        import httpx

        # GET only. See the module docstring: this script never writes.
        response = httpx.get(
            f"{url}/rest/v1/user_resumes",
            params={"select": "tex_content,created_at",
                    "order": "created_at.desc", "limit": args.baseline_limit},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=60,
        )
        response.raise_for_status()
        rows = [(r.get("tex_content") or "") for r in response.json()]
        note = f"{len(rows)} user_resumes rows (newest first) via httpx.get"
    if not rows:
        raise SystemExit("baseline is empty -- see the warning above.")
    # Mirrors shared.resume_format.BaseResume.all_tex exactly.
    return "\n".join(rows), rows[0], note


def _self_check(inspectable: list[tuple[str, str]], union: str) -> None:
    r"""Prove the arms can diverge on this corpus before trusting that they don't.

    CLAUDE.md rule 3: confirm the metric exists before reading zero as good
    news, and rule 12: fix the instrument before trusting the reading. "0 new
    flags" is the outcome this change wants, and it is also exactly what a
    broken harness prints -- a mis-imported rule, a frozen arm accidentally
    pointing at the live extractor, a corpus of empty strings.

    So: plant an unsupported blocklist token in the one position the bug hid,
    the leading entry of the first `\item`, and require the frozen arm to miss
    every planted document and the live arm to catch every one. If that does
    not hold, nothing else this script prints means anything.
    """
    planted = _PLANT
    assert not og._supported_by_base(planted.lower(), union.lower()), (
        f"{planted} is in the baseline, so it cannot be used as a planted "
        "fabrication -- pick a blocklist token the candidate has never claimed."
    )
    mutants = []
    for label, tex in inspectable:
        mutant, n = re.subn(
            r"(\\item\s*(?:\\textbf\{[^}]*\})?\s*)([^,&/()\n;:]+)",
            rf"\g<1>{planted}", tex, count=1,
        )
        if n:
            mutants.append((label, mutant))
    missed_by_frozen = [
        label for label, tex in mutants
        if not violations(tex, union.lower(), "frozen (pre-fix)")
    ]
    caught_by_live = [
        label for label, tex in mutants
        if any(s == planted.lower() for s, _ in violations(tex, union.lower(), "live (shipping)"))
    ]
    print(f"self-check: planted {planted!r} in the leading `\\item` entry of "
          f"{len(mutants)}/{len(inspectable)} documents -- "
          f"frozen rule misses {len(missed_by_frozen)}, "
          f"live rule catches {len(caught_by_live)}")
    if not mutants or len(missed_by_frozen) != len(mutants) or len(caught_by_live) != len(mutants):
        raise SystemExit(
            "self-check FAILED: the two arms do not diverge on the position "
            "this change is about, so every rate above is unreadable. Fix the "
            "harness before reading the measurement."
        )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("loader", "dir", "git"), default="loader")
    parser.add_argument("--n", type=int, default=740)
    parser.add_argument("--tex-dir")
    parser.add_argument("--baseline-tex", nargs="*", default=[])
    parser.add_argument("--baseline-limit", type=int, default=10)
    parser.add_argument("--no-self-check", dest="self_check", action="store_false")
    args = parser.parse_args()

    docs, corpus_note = load_corpus(args)
    union, newest, baseline_note = load_baselines(args)

    # Rule 2: a status that cannot tell "did the work" from "did nothing" is a
    # lie. An empty corpus flags nothing, which prints as a perfect 0%.
    if not docs:
        raise SystemExit("corpus is empty -- nothing was measured.")

    baselines = {"union of all rows": union, "newest row only": newest}
    # Rule 7: the population this check judges is documents it can actually
    # see into. A document with neither region is not a pass, it is not a
    # subject -- counting it dilutes every rate below.
    inspectable = [(label, tex) for label, tex in docs if og._fabrication_regions(tex)]

    print(f"corpus:   {corpus_note}")
    print(f"baseline: {baseline_note}")
    print(f"documents: {len(docs)} total, {len(inspectable)} with a region the "
          f"guard inspects (header subtitle and/or Skills section)")
    if args.source != "loader":
        print("NOT THE GATE: --source", args.source, "is not the real tailored "
              "corpus. Rule 16 needs --source loader.")
    print()

    # Visibility, before the support filter. Without this the table below is
    # uninterpretable: 0 flagged reads identically for "the widening found
    # nothing unsupported" and "the widening does nothing at all" (rule 3 --
    # confirm the metric can move before reading zero as good news, and
    # rule 12 -- check the instrument first). These counts are the widening
    # itself; the table is the widening's cost.
    visible = {}
    for rule in RULES:
        regions, claims = RULES[rule]
        visible[rule] = sorted(
            {(label, region, skill)
             for label, tex in inspectable
             for region, text in regions(tex)
             for skill in claims(text)}
        )
    for rule in RULES:
        tokens = sorted({s for _, _, s in visible[rule]})
        print(f"{rule:<18} sees {len(visible[rule]):>3} blocklisted claims "
              f"across the corpus: {tokens}")
    # How big the widening is, independent of what it happened to find. These
    # are the strings the frozen rule could not see -- the leading entry of
    # every `\item` in an inspected region. "0 newly visible claims" is only
    # good news next to a non-trivial count here; on its own it is equally
    # consistent with a corpus that has no `\item` lines at all.
    exposed = [entry for _, tex in inspectable for entry in _leading_entries(tex)]
    blocklisted = [e for e in exposed if e.strip().lower() in og._KNOWN_FABRICATIONS]
    print(f"leading `\\item` entries the frozen rule could not see: {len(exposed)}"
          f" across {len(inspectable)} documents; {len(blocklisted)} blocklisted"
          f" {sorted(set(blocklisted))}")

    gained = sorted(set(visible["live (shipping)"]) - set(visible["frozen (pre-fix)"]))
    lost_sight = sorted(
        set(visible["frozen (pre-fix)"]) - set(visible["live (shipping)"]))
    print(f"newly VISIBLE to the live rule: {len(gained)} "
          f"{sorted({(r, s) for _, r, s in gained})}")
    if lost_sight:
        print(f"NO LONGER VISIBLE (a miss the fix introduced): {lost_sight}")
    print()

    if args.self_check:
        _self_check(inspectable, union)

    results = {}
    for rule in RULES:
        for baseline_name, base_tex in baselines.items():
            base_lower = base_tex.lower()
            flagged = {
                label: violations(tex, base_lower, rule)
                for label, tex in inspectable
            }
            flagged = {k: v for k, v in flagged.items() if v}
            results[(rule, baseline_name)] = flagged
            n = len(inspectable)
            rate = f"{len(flagged) / n:.1%}" if n else "n/a"
            tokens = sorted({s for v in flagged.values() for s, _ in v})
            print(f"{rule:<18} x {baseline_name:<18} "
                  f"{len(flagged):>4}/{n:<4} ({rate:>6})  tokens={tokens}")

    print()
    for baseline_name in baselines:
        before = results[("frozen (pre-fix)", baseline_name)]
        after = results[("live (shipping)", baseline_name)]
        new_docs = sorted(set(after) - set(before))
        print(f"--- newly flagged against the {baseline_name} baseline: "
              f"{len(new_docs)}/{len(inspectable)} documents "
              "(adjudicate each: caught fabrication, or false positive?)")
        for label in new_docs:
            print(f"    {label}: {after[label]}")
        lost = sorted(set(before) - set(after))
        if lost:
            print(f"    REGRESSION -- flagged before, not now: {lost}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
