"""Per-application extraction from a weekly-list document.

The watch agent detects that a new list was published. This turns that list
into the individual applications inside it, so an alert can say what is
proposed, where, and by when an observation must be made.

The division of labour is the same one the rest of the watch agent keeps, and
it is what makes the output trustworthy:

  * **The model reads. It does not compute.** It extracts rows from the PDF
    text and copies the printed receipt date verbatim. It is told not to
    calculate a deadline, and it never sees one.
  * **Deadlines are arithmetic**, done in `core.weekly_list` from the date the
    model copied out. A model cannot invent a date it does not produce.
  * **Extraction is checked against the document.** File numbers are counted
    by regex independently; if the model returns materially fewer rows, the
    result says the list is partial rather than implying it is complete.

With no API key the whole thing degrades to a regex pass that still recovers
file numbers and receipt dates - fewer fields, same deadlines, same citations.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field

from core.weekly_list import (
    count_file_numbers,
    observation_deadline,
    parse_ddmmyyyy,
    parse_receipt_window,
)

log = logging.getLogger(__name__)

# A weekly list is a few pages; send it in slices so a long one still extracts
# fully without one oversized request.
CHUNK_CHARS = 6000
MAX_CHUNKS = 6
MAX_APPLICATIONS = 60

# Judged against the independent regex count.
COMPLETENESS_THRESHOLD = 0.7


class ListedApplication(BaseModel):
    """One row of a weekly list, as printed."""

    file_number: str = Field(
        min_length=1,
        description="The council's file or reference number, exactly as printed, e.g. 26/60461.",
    )
    date_received: str = Field(
        description="The printed DATE RECEIVED, copied exactly, e.g. 31/08/2026. Empty if absent.",
    )
    description: str = Field(
        min_length=1,
        description="The development description, as printed. Do not summarise or rewrite.",
    )
    location: str = Field(
        default="",
        description="The development location or address, as printed.",
    )
    applicant: str = Field(default="", description="The applicant's name, as printed.")
    application_type: str = Field(
        default="",
        description="The application type code as printed, e.g. P, R, E, O.",
    )
    protected_structure: bool = Field(
        default=False,
        description="True only if the protected-structure column is Y for this row.",
    )


class _Extraction(BaseModel):
    applications: list[ListedApplication] = Field(default_factory=list)


EXTRACT_SYSTEM = """You extract planning application rows from an Irish council weekly list.

Rules:
1. Use ONLY the text supplied. Never infer, complete or invent a row.
2. Copy `date_received` exactly as printed, in the document's own DD/MM/YYYY
   form. Do not reformat it and do not calculate anything from it.
3. Do NOT compute or mention any deadline. Deadlines are calculated elsewhere
   from the date you copy.
4. Copy the development description as printed. Do not summarise it, tidy it,
   or make it read better.
5. A description and location that wrap across several lines belong to the file
   number above them. Join them; do not emit them as separate applications.
6. In these lists the description and the site address share one column, with
   the address on the trailing lines. Put the address in `location` and leave
   it out of `description`, so the two fields do not repeat each other.
7. `protected_structure` is true only where that column is explicitly Y.
8. Skip page headers, footers, column headings and the data-protection notice.
9. If a field is not printed, leave it empty rather than guessing."""

# These lists print four flag columns after the description - EIS, protected
# structure, IPC licence, waste licence - as bare Y/N. Text extraction pulls
# them onto the end of the description, so "...dwelling N N N" is the norm.
# Stripped deterministically rather than asked of the model, because a regex
# cannot decide to leave one in.
_TRAILING_FLAGS_RE = re.compile(r"(?:\s+[YN](?=\s|$)){1,4}\s*$", re.I)


def _strip_flag_columns(text: str) -> str:
    """Remove the trailing Y/N flag columns from an extracted description."""
    cleaned = _TRAILING_FLAGS_RE.sub("", (text or "").strip())
    return cleaned.strip(" .,;-") or (text or "").strip()


# Fallback pass: file number, type code, date, then the description tail.
_ROW_RE = re.compile(
    r"^(?P<ref>\d{2}/\d{2,6})\s+(?P<rest>.+?)\s+(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<desc>.*)$",
    re.M,
)


# Dublin-style lists, once `core.weekly_list` has split the inline labels, come
# out as labelled blocks rather than fixed-width rows:
#
#   Application Number: WEB2338/26
#   Application Type: Permission
#   Applicant: Tom Doone
#   Location: 21 Baggot Street
#   Registration Date: 24/08/2026
#   Proposal: Change of use from ...
#
# Parsed deterministically, so the no-key path works for Dublin as well as for
# Westmeath's tabular PDFs.
_LABELLED_SPLIT_RE = re.compile(r"(?=^Application Number:)", re.M)


def _labelled_field(block: str, label: str) -> str:
    match = re.search(
        rf"^{re.escape(label)}:\s*(.+?)\s*$(?!\n[A-Z][a-z]+ ?[A-Za-z]*:)",
        block,
        re.M,
    )
    if match:
        return match.group(1).strip()

    # A value that wraps onto following lines runs until the next label.
    match = re.search(
        rf"^{re.escape(label)}:\s*(.*?)(?=\n[A-Z][A-Za-z ]{{2,24}}:|\Z)",
        block,
        re.M | re.S,
    )
    return " ".join(match.group(1).split()) if match else ""


def _labelled_applications(text: str) -> list[ListedApplication]:
    """Parse labelled blocks, as Dublin publishes them."""
    found: list[ListedApplication] = []

    for block in _LABELLED_SPLIT_RE.split(text or ""):
        if not block.lstrip().startswith("Application Number:"):
            continue

        reference = _labelled_field(block, "Application Number")
        if not reference:
            continue

        description = (
            _labelled_field(block, "Proposal")
            or _labelled_field(block, "Development Description")
            or "Description not recovered from the published list."
        )

        found.append(ListedApplication(
            file_number=reference,
            date_received=_labelled_field(block, "Registration Date"),
            description=description[:1200],
            location=_labelled_field(block, "Location")[:300],
            applicant=_labelled_field(block, "Applicant")[:160],
            application_type=_labelled_field(block, "Application Type")[:60],
            protected_structure="protected structure" in description.lower(),
        ))

        if len(found) >= MAX_APPLICATIONS:
            break

    return found


def _regex_applications(text: str) -> list[ListedApplication]:
    """Deterministic extraction. Fewer fields, but the reference and date are
    what the deadline depends on, and both are recovered here."""
    found: list[ListedApplication] = []

    for match in _ROW_RE.finditer(text or ""):
        description = (match.group("desc") or "").strip()
        if not description:
            description = "Description not recovered from the published list."

        found.append(ListedApplication(
            file_number=match.group("ref"),
            date_received=match.group("date"),
            description=description[:400],
            applicant=(match.group("rest") or "").strip()[:120],
        ))

        if len(found) >= MAX_APPLICATIONS:
            break

    return found


def _llm_applications(text: str) -> list[ListedApplication] | None:
    """Extract with the model, in slices. None if unavailable or it fails."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    base_url = os.getenv("PLANPERM_LLM_BASE_URL")
    model = os.getenv("PLANPERM_LLM_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))

    # Slice on line boundaries so a row is never cut in half.
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in (text or "").splitlines():
        current.append(line)
        size += len(line) + 1
        if size >= CHUNK_CHARS:
            chunks.append("\n".join(current))
            current, size = [], 0
    if current:
        chunks.append("\n".join(current))

    collected: list[ListedApplication] = []
    seen: set[str] = set()
    failures = 0

    for chunk in chunks[:MAX_CHUNKS]:
        try:
            completion = client.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": EXTRACT_SYSTEM},
                    {"role": "user", "content": chunk},
                ],
                response_format=_Extraction,
                temperature=0,
            )
            parsed = completion.choices[0].message.parsed
        except Exception as exc:                  # noqa: BLE001 - keep what we have
            log.info("Weekly-list extraction failed for one slice: %s", exc)
            failures += 1
            continue

        if parsed is None:
            failures += 1
            continue

        for application in parsed.applications:
            reference = application.file_number.strip()
            if not reference or reference in seen:
                continue
            seen.add(reference)
            collected.append(application)

    if failures and not collected:
        return None

    return collected


def extract_applications(text: str, today=None) -> dict[str, Any]:
    """Turn a weekly-list document into applications with deadlines.

    Returns {applications, method, expected, complete, window}. Each
    application carries a `deadline` computed from its own printed receipt
    date, or None where no usable date was printed.
    """
    expected = count_file_numbers(text)
    window_from, window_to = parse_receipt_window(text)

    applications = _llm_applications(text)
    method = "llm"

    if not applications:
        # Labelled blocks (Dublin) and fixed-width rows (Westmeath) are both
        # in use, so try each rather than assuming one layout.
        applications = _labelled_applications(text)
        method = "regex-labelled"

    if not applications:
        applications = _regex_applications(text)
        method = "regex-tabular"

    enriched: list[dict[str, Any]] = []
    for application in applications[:MAX_APPLICATIONS]:
        record = application.model_dump()
        record["description"] = _strip_flag_columns(record.get("description", ""))
        record["location"] = _strip_flag_columns(record.get("location", ""))

        # The deadline is arithmetic on the date the model copied out, never
        # something the model produced.
        record["deadline"] = observation_deadline(application.date_received, today=today)
        record["date_received_iso"] = (
            parsed.isoformat() if (parsed := parse_ddmmyyyy(application.date_received)) else None
        )
        enriched.append(record)

    # Judged against the document itself, not against the model's confidence.
    complete = bool(expected) and len(enriched) >= expected * COMPLETENESS_THRESHOLD

    return {
        "applications": enriched,
        "method": method,
        "expected": expected,
        "extracted": len(enriched),
        "complete": complete if expected else None,
        "window": {
            "from": window_from.isoformat() if window_from else None,
            "to": window_to.isoformat() if window_to else None,
        },
    }


def summarise_extraction(result: dict[str, Any]) -> str:
    """One honest line about what was read out of the document."""
    extracted = result["extracted"]
    expected = result["expected"]

    if not extracted:
        return "No application rows could be read from this document."

    line = f"Read {extracted} application(s) from the published list"
    # Only call it partial when it actually failed the completeness check -
    # saying "partial" while also reporting complete=True contradicts itself.
    if expected and result.get("complete") is False:
        line += (
            f" — but {expected} file numbers appear in the document, so this is "
            f"incomplete"
        )
    elif expected and extracted < expected:
        line += f" of {expected} file numbers found in the document"
    line += f", extracted by {result['method']}."

    window = result.get("window") or {}
    if window.get("from") and window.get("to"):
        line += f" The list covers applications received {window['from']} to {window['to']}."

    return line
