"""Weekly-list documents: fetching, and the statutory date arithmetic.

Everything in this module is deterministic. The published PDF is fetched, its
text extracted, its receipt window read by regex, and observation deadlines
computed by date arithmetic. No model is involved, so none of these values can
be invented - which matters because a wrong deadline is the one error that
could cost somebody their chance to make a submission.

The counting rule is the part that is easy to get wrong. Irish planning
submissions must be made "within the period of 5 weeks beginning on the date
of receipt of the application", and a period *beginning on* a date includes
that date as day one. So the last day is `received + 34 days`, not
`received + 35`.

Every result is labelled an estimate and carries the arithmetic that produced
it. The authority is the council's own file, not this subtraction: fees,
invalid applications, further-information requests and extensions all move
real dates, and none of that is in a weekly list.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta

import requests

log = logging.getLogger(__name__)

OBSERVATION_WEEKS = 5
# "Beginning on" the receipt date makes that date day one, so a 5-week period
# ends 34 days later, not 35.
OBSERVATION_DAYS = OBSERVATION_WEEKS * 7 - 1
CLOSING_SOON_DAYS = 7

FETCH_TIMEOUT = 40
MAX_BYTES = 12 * 1024 * 1024
MAX_PAGES = 20
USER_AGENT = "PlanPermitCompass/0.2 local source monitor"

# "PLANNING APPLICATIONS RECEIVED FROM 31/08/2026 To 06/09/2026"
_WINDOW_RE = re.compile(
    r"RECEIVED\s+FROM\s+(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})", re.I
)

# Dublin's form: "(24/08/2026-30/08/2026)" near the top of the document.
_WINDOW_DASH_RE = re.compile(
    r"\(\s*(\d{2}/\d{2}/\d{4})\s*[-–—]\s*(\d{2}/\d{2}/\d{4})\s*\)"
)

# Two shapes in the wild: Westmeath "26/60461" (year first) and Dublin
# "5001/26" / "WEB2338/26" (year last, sometimes prefixed).
#
# The lookbehind and the year exclusion matter: a printed date like
# "31/08/2026" contains "08/2026", which fits the reference shape. Counting
# those inflated the expected total and made the completeness check report
# "incomplete" for a list that had been read in full - discrediting the one
# signal that tells a reader whether to trust the extraction.
_FILE_NUMBER_RE = re.compile(
    r"(?<![\d/])\b((?:WEB)?\d{2,6}/(?!(?:19|20)\d{2}\b)\d{2,6})(?=\s|$)",
    re.M | re.I,
)

# Titles of documents that list newly received applications - the only ones
# with an observation window still to run.
_RECEIVED_HINTS = ("received application", "applications received",
                   "planning applications received")

# Authorities that publish one combined weekly list rather than a separate
# "received" list. Dublin's files are a1-wpl-35-26.docx with the link text
# "Download DOCX (0.2MB)", so nothing in the title identifies them - the
# filename is the only signal, and a combined list does contain received
# applications.
_WEEKLY_LIST_PATTERNS = (
    "wpl",
    "weekly-planning-list",
    "weeklyplanninglist",
    "weekly-list",
    "weeklylist",
)


def is_received_list(title: str, url: str = "") -> bool:
    """Is this document a list of newly received applications?

    Only these carry an open observation window. A "Granted applications" or
    "Refused applications" list is a record of decisions already made, and
    attaching a submission deadline to one would be wrong.
    """
    haystack = f"{title} {url}".lower()
    if any(hint in haystack for hint in _RECEIVED_HINTS):
        return True

    # A combined weekly list includes the received applications, so it counts.
    filename = haystack.split("/")[-1]
    return any(pattern in filename for pattern in _WEEKLY_LIST_PATTERNS)


def parse_ddmmyyyy(value: str) -> date | None:
    """Parse a date exactly as Irish councils print it. None if unrecognised."""
    text = (value or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _extract_docx_text(payload: bytes) -> str:
    """Text from a .docx, using only the standard library.

    A .docx is a zip of XML, so this needs no dependency at all. It matters
    because Dublin City Council - the largest planning authority in the state -
    publishes its weekly lists as Word documents, not PDFs. Supporting only
    PDF meant the watch agent silently found nothing for Dublin.
    """
    import zipfile
    from xml.etree import ElementTree

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            document = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        log.info("Could not open the document as .docx: %s", exc)
        return ""

    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        log.info("Could not parse the .docx body: %s", exc)
        return ""

    def cell_text(cell) -> str:
        return " ".join(
            "".join(run.text or "" for run in paragraph.iter(namespace + "t")).strip()
            for paragraph in cell.iter(namespace + "p")
        ).strip()

    # Walk the body in document order. Tables must be read cell by cell with a
    # separator: Dublin lays each application out as a label/value table, and
    # concatenating the cells blindly produces
    # "AreaArea 1 - South EastApplication NumberWEB2338/26..." - unparsable by
    # a model or a regex. Emitting " | " between cells keeps the fields apart.
    body = root.find(namespace + "body")
    if body is None:
        body = root

    lines: list[str] = []
    seen_in_tables: set = set()

    for table in body.iter(namespace + "tbl"):
        for paragraph in table.iter(namespace + "p"):
            seen_in_tables.add(id(paragraph))

    for element in body.iter():
        tag = element.tag

        if tag == namespace + "tbl":
            for row in element.iter(namespace + "tr"):
                cells = [cell_text(cell) for cell in row.iter(namespace + "tc")]
                cells = [c for c in cells if c]
                if cells:
                    lines.append(" | ".join(cells))

        elif tag == namespace + "p" and id(element) not in seen_in_tables:
            text = "".join(run.text or "" for run in element.iter(namespace + "t"))
            if text.strip():
                lines.append(_split_inline_labels(text.strip()))

    return "\n".join(lines)


# Dublin writes each application as one paragraph with the field labels inline
# and no separators at all:
#   "AreaArea 1 - South EastApplication Number5001/26Application TypePermission..."
# The labels are fixed, so putting a break before each one restores the
# structure for both the model and the regex fallback. Longest first, so
# "Application Number" is matched before "Application".
_INLINE_LABELS = (
    "Application Number",
    "Application Type",
    "Registration Date",
    "Additional Information",
    "Submission Type",
    "Decision Date",
    "Applicant",
    "Location",
    "Proposal",
    "Decision",
    "Area",
)

_INLINE_LABEL_RE = re.compile(
    "(" + "|".join(re.escape(label) for label in _INLINE_LABELS) + ")"
)


def _split_inline_labels(text: str) -> str:
    """Put a separator before each inline field label."""
    if "Application Number" not in text:
        return text

    parts = _INLINE_LABEL_RE.split(text)
    rebuilt: list[str] = []

    for part in parts:
        if not part:
            continue
        if part in _INLINE_LABELS:
            rebuilt.append(f"\n{part}: ")
        else:
            rebuilt.append(part.strip(": ").strip())

    return "".join(rebuilt).strip()


def _extract_pdf_text(payload: bytes) -> tuple[str, int]:
    """Text and page count from a PDF.

    Prefers pdfplumber, which preserves column layout - these lists are tables,
    and Westmeath's collapses into unparsable soup without it. Falls back to
    pypdf, which the project already depends on, so the agent still works on a
    plain install where pdfplumber is absent.
    """
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None

    if pdfplumber is not None:
        try:
            with pdfplumber.open(io.BytesIO(payload)) as pdf:
                pages = pdf.pages[:MAX_PAGES]
                text = "\n".join((page.extract_text() or "") for page in pages)
                if text.strip():
                    return text, len(pages)
        except Exception as exc:                  # noqa: BLE001 - malformed PDFs are common
            log.info("pdfplumber could not read the document: %s", exc)

    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("Neither pdfplumber nor pypdf is installed; cannot read PDFs.")
        return "", 0

    try:
        reader = PdfReader(io.BytesIO(payload))
        pages = reader.pages[:MAX_PAGES]
        return "\n".join((page.extract_text() or "") for page in pages), len(pages)
    except Exception as exc:                      # noqa: BLE001
        log.info("pypdf could not read the document either: %s", exc)
        return "", 0


def fetch_document_text(url: str) -> dict | None:
    """Fetch a published list document and extract its text. None if unreadable.

    Handles PDF and .docx, because Irish authorities publish both - Westmeath
    uses PDF, Dublin uses Word.

    Returns {text, pages, bytes, scanned}. Failure is returned rather than
    raised: a document that cannot be read must degrade the alert, not break
    the scan.
    """
    if not url.startswith(("http://", "https://")):
        return None

    try:
        response = requests.get(
            url, timeout=FETCH_TIMEOUT, stream=True, headers={"User-Agent": USER_AGENT}
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        log.info("Could not fetch %s: %s", url, exc)
        return None

    chunks, total = [], 0
    for chunk in response.iter_content(64 * 1024):
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_BYTES:
            log.info("Document %s exceeds %s bytes; truncating.", url, MAX_BYTES)
            break
    payload = b"".join(chunks)

    content_type = (response.headers.get("content-type") or "").lower()
    path = url.lower().split("?", 1)[0]

    if path.endswith((".docx", ".doc")) or "wordprocessingml" in content_type:
        text = _extract_docx_text(payload)
        # A Word document has no fixed pagination, so a page tally would be
        # invented; report 1 when there is text at all.
        page_count = 1 if text.strip() else 0
    else:
        text, page_count = _extract_pdf_text(payload)

    if not text.strip():
        # Almost certainly a scan. OCR is out of scope here; say so rather than
        # reporting an empty list as "no applications".
        return {"text": "", "pages": page_count, "bytes": total, "scanned": True}

    return {"text": text, "pages": page_count, "bytes": total, "scanned": False}


def parse_receipt_window(text: str) -> tuple[date | None, date | None]:
    """The "RECEIVED FROM x To y" window printed on the list, if present."""
    for pattern in (_WINDOW_RE, _WINDOW_DASH_RE):
        match = pattern.search(text or "")
        if match:
            return parse_ddmmyyyy(match.group(1)), parse_ddmmyyyy(match.group(2))
    return None, None


def count_file_numbers(text: str) -> int:
    """How many application rows the text appears to contain.

    A cheap, independent check on the model's extraction: if it returns far
    fewer applications than there are file numbers, something was missed and
    the alert should say so rather than implying the list is complete.
    """
    return len(set(_FILE_NUMBER_RE.findall(text or "")))


def observation_deadline(date_received: str | date, today: date | None = None) -> dict | None:
    """The observation window for one application. None if the date is unusable."""
    received = date_received if isinstance(date_received, date) else parse_ddmmyyyy(str(date_received))
    if received is None:
        return None

    today = today or date.today()
    closes = received + timedelta(days=OBSERVATION_DAYS)
    days_left = (closes - today).days

    return {
        "kind": "observation",
        "label": "Observation / submission deadline",
        "received": received.isoformat(),
        "closes": closes.isoformat(),
        "days_left": days_left,
        "is_open": days_left >= 0,
        "closing_soon": 0 <= days_left <= CLOSING_SOON_DAYS,
        "estimated": True,
        # The audit trail: how this date was reached, in words a reader can check.
        "basis": (
            f"{OBSERVATION_WEEKS} weeks beginning on the date the authority received "
            f"the application ({received.isoformat()}). A period beginning on a date "
            f"counts that date as day one, so the window closes {OBSERVATION_DAYS} "
            f"days later."
        ),
        "caveat": (
            "Estimated from the date printed on the published weekly list. Confirm "
            "with the planning authority - invalid applications, further-information "
            "requests and extensions all move the real date."
        ),
    }


def describe_deadline(deadline: dict) -> str:
    """One human-readable line."""
    if not deadline["is_open"]:
        return f"Observations closed {deadline['closes']} (estimated)"

    days = deadline["days_left"]
    if days == 0:
        when = "today"
    elif days == 1:
        when = "tomorrow"
    else:
        when = f"in {days} days"

    return f"Observations close {deadline['closes']} - {when} (estimated)"
