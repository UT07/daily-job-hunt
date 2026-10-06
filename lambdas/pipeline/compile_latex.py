import logging
import os
import subprocess
import tempfile

import boto3

from shared.composition_policy import resolve
from shared.ats_extract_check import check_ats_extraction
from shared.ats_extract_check import log_violations as log_ats_violations
from shared.fit_to_pages import fit, normalise_separators
from shared.page_check import check_pdf, log_violations, page_text_lengths
from utils.pdf_validator import check_file_size

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# tectonic is provided via the TectonicLayer Lambda Layer, which extracts to /opt/.
# The binary is at /opt/bin/tectonic. We fall back to bare "tectonic" for local runs.


def handler(event, context):
    tex_s3_key = event["tex_s3_key"]
    job_hash = event.get("job_hash", "")
    user_id = event.get("user_id", "")
    doc_type = event.get("doc_type", "resume")

    s3 = boto3.client("s3")
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    # Read tex from S3
    obj = s3.get_object(Bucket=bucket, Key=tex_s3_key)
    tex_content = obj["Body"].read().decode("utf-8")

    # Separator normalisation, before anything is measured or rendered.
    # Reported 2026-10-05 and visible in every entry: \jobentry is defined as
    # `\textbf{#1} -- #2 \hfill \textit{#3}` and the corpus passes #2 (the
    # LOCATION) empty, so each role rendered "Yuno Energy --" with a dash
    # attached to nothing. The date separator was also U+2013; a hyphen is what
    # the user asked for and the safer character, since an en-dash depends on
    # the font and the input encoding surviving every hop to the PDF.
    #
    # Applied here rather than at generation so it repairs the documents
    # already in S3, which is where the ones the user is looking at live.
    if doc_type == "resume":
        tex_content, sep_actions = normalise_separators(tex_content)
        if sep_actions:
            logger.info("[compile] separators for %s: %s",
                        job_hash, "; ".join(sep_actions))

    # Write to temp file and compile
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "document.tex")
        with open(tex_path, "w") as f:
            f.write(tex_content)

        # Resolve tectonic binary: Lambda Layer extracts to /opt/bin/tectonic
        tectonic_path = "/opt/bin/tectonic" if os.path.exists("/opt/bin/tectonic") else "tectonic"

        try:
            # tectonic needs writable cache — Lambda only allows /tmp
            env = os.environ.copy()
            env["XDG_CACHE_HOME"] = "/tmp"
            env["HOME"] = "/tmp"

            # /tmp is empty on a cold execution environment, so XDG_CACHE_HOME
            # above starts with no tectonic bundle cache: the first compile in
            # a fresh container has to fetch the whole LaTeX package bundle
            # over the network before it can compile anything. 45s was only
            # ever enough for a warm container with the bundle already
            # cached, and this stack can go idle for weeks between
            # invocations (e.g. the pipeline being parked), so a cold
            # container is the common case, not the edge case. 110s covers a
            # cold fetch + compile; CompileLatexFunction's own Timeout in
            # template.yaml is raised alongside this so the Lambda outlives
            # the subprocess instead of being hard-killed first.
            result = subprocess.run(
                [tectonic_path, "-X", "compile", tex_path],
                capture_output=True, text=True, timeout=110,
                env=env,
            )
            if result.returncode != 0:
                logger.error(f"[compile] tectonic failed: {result.stderr}")
                return {"error": "compilation_failed", "stderr": result.stderr[:500],
                        "tex_s3_key": tex_s3_key, "job_hash": job_hash,
                        "user_id": user_id, "doc_type": doc_type}

            pdf_path = os.path.join(tmpdir, "document.pdf")
            if not os.path.exists(pdf_path):
                return {"error": "no_pdf_output", "tex_s3_key": tex_s3_key,
                        "job_hash": job_hash, "user_id": user_id, "doc_type": doc_type}

            # --- Page check: the rule that only the compiled PDF can answer ---
            #
            # This replaces a call to utils.pdf_validator.validate_pdf that had
            # never run. It needs `fitz` (pymupdf), which is in no requirements
            # file in this repo, so every invocation took the ImportError path,
            # logged "PDF validation skipped" at warning level and returned
            # valid=True. Its page-count check has therefore never once
            # executed in production, which is why a resume with a blank page
            # in the middle cleared this function.
            #
            # shared.page_check uses pdfplumber (requirements.txt,
            # requirements-web.txt, and layer/requirements.txt so it is on this
            # zip Lambda's path) and reports a violation when it cannot run,
            # rather than reporting success.
            #
            # `pages` is not threaded through either state machine, so this
            # falls back to composition_policy's default of 2 — which is what
            # every user has today. Passing `pages` in the event overrides it
            # for when a per-user policy does get threaded through.
            expected_pages = event.get("pages")
            if expected_pages is None and doc_type == "resume":
                expected_pages = resolve(None)["pages"]
            page_violations = check_pdf(pdf_path, expected_pages=expected_pages)
            log_violations(page_violations, label=f"{doc_type} {job_hash}")

            # --- Act on the measurement, rather than only reporting it ------
            #
            # Until 2026-10-06 this function measured the PDF, logged the
            # violation and uploaded the document anyway -- the same shape the
            # composition check had before #181, and the reason the user's
            # resume was FOUR pages against a policy of two.
            #
            # Measured on the live corpus composed to the 3+3 policy: full
            # content is 3 pages even at 0.50/0.60in margins, so two pages is
            # not reachable by layout and content must go. shared.fit_to_pages
            # pulls the levers cheapest-first -- margins cost nothing, then
            # entry bullets, then skills LAST because those are ATS keyword
            # matches -- recompiling after each, and returns the original
            # untouched if nothing reaches the budget.
            #
            # Bounded by WALL CLOCK, not just by step count. Each attempt is a
            # tectonic run, this function already allows 110s for one of them,
            # and a retry loop bounded only by attempts is unbounded in the
            # dimension that actually ends the invocation (the same defect as
            # the council's critic retry, fixed the same week).
            fit_actions: list[str] = []
            if page_violations and doc_type == "resume":
                attempts = {"n": 0}

                def _measure(candidate: str) -> int:
                    attempts["n"] += 1
                    cand_tex = os.path.join(tmpdir, f"fit{attempts['n']}.tex")
                    with open(cand_tex, "w") as fh:
                        fh.write(candidate)
                    r = subprocess.run(
                        [tectonic_path, "-X", "compile", cand_tex],
                        capture_output=True, text=True, timeout=110, env=env,
                    )
                    cand_pdf = cand_tex.replace(".tex", ".pdf")
                    if r.returncode != 0 or not os.path.exists(cand_pdf):
                        # A candidate that will not compile is not a smaller
                        # document; report it as over budget so the loop moves
                        # on instead of treating a build failure as a fit.
                        return 10**6
                    return len(page_text_lengths(cand_pdf))

                def _time_left() -> float:
                    if context is None or not hasattr(context, "get_remaining_time_in_millis"):
                        return float("inf")
                    return context.get_remaining_time_in_millis() / 1000.0

                def _measure_guarded(candidate: str) -> int:
                    # 40s: one more tectonic run plus the S3 writes below.
                    if _time_left() < 40:
                        logger.warning(
                            "[compile] stopping page fit for %s with %.0fs left",
                            job_hash, _time_left())
                        return 10**6
                    return _measure(candidate)

                try:
                    fitted, fit_actions, fits = fit(tex_content, _measure_guarded)
                except Exception as exc:  # noqa: BLE001 - never fail a compile over this
                    logger.warning("[compile] page fit raised for %s: %s", job_hash, exc)
                    fitted, fit_actions, fits = tex_content, [], False

                if fits and fit_actions:
                    logger.info("[compile] fitted %s to %s page(s): %s",
                                job_hash, expected_pages, "; ".join(fit_actions))
                    tex_content = fitted
                    with open(tex_path, "w") as fh:
                        fh.write(fitted)
                    subprocess.run([tectonic_path, "-X", "compile", tex_path],
                                   capture_output=True, text=True, timeout=110, env=env)
                    # The .tex is the Studio's source of truth, so it must carry
                    # the document that was actually shipped -- otherwise the
                    # editor opens content that does not match the PDF beside it.
                    s3.put_object(Bucket=bucket, Key=tex_s3_key,
                                  Body=tex_content.encode("utf-8"))
                    page_violations = check_pdf(pdf_path, expected_pages=expected_pages)
                elif fit_actions or not fits:
                    logger.warning(
                        "[compile] could NOT fit %s to %s page(s) after %d attempt(s); "
                        "shipping it over budget rather than half-trimmed",
                        job_hash, expected_pages, attempts["n"])

            # --- Does our own PDF survive the way an ATS reads it? ---------
            #
            # Nothing asked this before 2026-10-06. The pipeline validated the
            # LaTeX source and the page count, then shipped a document whose
            # entire purpose is to be parsed by software nobody had pointed at
            # it. Run AFTER fitting, because fitting is what last changed the
            # layout, and a check on the pre-fit document would describe a file
            # that was never uploaded.
            ats_violations: list[str] = []
            if doc_type == "resume":
                ats_violations = check_ats_extraction(pdf_path, tex_content)
                log_ats_violations(ats_violations, label=f"{doc_type} {job_hash}")

            size_issue = check_file_size(os.path.getsize(pdf_path))
            if size_issue:
                logger.warning(f"[compile] PDF file size for {job_hash}: {size_issue}")

            # Upload PDF to S3
            pdf_key = tex_s3_key.replace(".tex", ".pdf")
            with open(pdf_path, "rb") as f:
                s3.put_object(Bucket=bucket, Key=pdf_key, Body=f.read(), ContentType="application/pdf")

            logger.info(f"[compile] {doc_type} PDF: {pdf_key}")
            # page_violations is always present: an empty list is the claim that
            # the PDF was measured and complies. A missing key would be
            # indistinguishable from a check that never ran — the exact failure
            # this block replaces.
            return {"job_hash": job_hash, "pdf_s3_key": pdf_key, "user_id": user_id,
                    "doc_type": doc_type, "page_violations": page_violations,
                    # Always present, like page_violations: an empty list is the
                    # claim that nothing needed cutting, which is different from
                    # a fit that was never attempted.
                    "fit_actions": fit_actions,
                    "ats_violations": ats_violations}

        except FileNotFoundError:
            # tectonic binary not available in this runtime
            logger.warning("[compile] tectonic not available - returning tex key only (no PDF compiled)")
            return {
                "job_hash": job_hash,
                "pdf_s3_key": None,
                "tex_s3_key": tex_s3_key,
                "user_id": user_id,
                "doc_type": doc_type,
                "error": "tectonic_not_available",
            }
