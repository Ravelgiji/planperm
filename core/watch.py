"""Manual source-index comparison. It flags measurable changes, never planning conclusions.

RECOVERED MODULE. The original source was gitignored and lost; this is a
reconstruction from the committed `core/__pycache__/watch.cpython-312.pyc`.
Function signatures, docstrings, string constants and the design intent are
recovered exactly. The control flow is reconstructed to match that evidence,
so it is faithful in behaviour but not guaranteed byte-identical to what was
written originally.

The design stance is deliberate and worth preserving: this compares an
official weekly-list index against a stored baseline and reports what
measurably appeared or disappeared. It does not interpret a document, judge a
proposal, or infer an observation deadline. Every alert points back at the
primary source and tells the reader to verify it there.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Only links that look like planning-list material are indexed. A weekly-list
# page is mostly navigation; without this filter the fingerprint churns on
# every menu change and every scan reports noise.
#
# DEVIATION FROM THE RECOVERED ORIGINAL. The original matched any href
# containing "weekly", "planning", ".pdf", ".doc" or "list". On a real council
# site every navigation link sits under /planning/, so that indexed the whole
# menu: a test scan of galwaycity.ie reported "English" and "Planning" as new
# planning documents. The filter below requires a positive document signal and
# rejects known navigation, which is the difference between a usable alert and
# noise. `_INDEX_HINTS` is kept for reference.
_INDEX_HINTS = ("weekly", "planning", ".pdf", ".doc", "list")

# A link is indexed if it points at a document...
_DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".rtf")

# ...or its href looks like a dated or numbered list page.
_LIST_MARKERS = ("weeklylist", "weekly-list", "weekly_list", "planninglist",
                 "planning-list", "decisionlist", "decision-list", "applist")

# Titles that are navigation furniture rather than documents.
_NAVIGATION_TITLES = {
    "english", "gaeilge", "irish", "home", "homepage", "planning", "search",
    "contact", "contact us", "about", "about us", "services", "news", "menu",
    "back", "next", "previous", "more", "login", "log in", "register",
    "accessibility", "privacy", "cookies", "sitemap", "skip to content",
    "planning services", "planning department", "apply", "help", "faq", "faqs",
}

_MIN_TITLE_LENGTH = 4
_TITLE_LIMIT = 80
_FALLBACK_TITLE = "Planning-list source document"


def _looks_like_document(href: str, title: str) -> bool:
    """Is this link plausibly a planning-list document rather than navigation?

    Requires a positive signal. Defaulting to "index it" is what made the
    original report menu items as planning documents.
    """
    lowered_href = href.lower()
    lowered_title = title.strip().lower()

    if lowered_title in _NAVIGATION_TITLES:
        return False

    # Strip the query string before testing the extension, so
    # "list.pdf?download=1" still reads as a PDF.
    path = lowered_href.split("?", 1)[0].split("#", 1)[0]
    if path.endswith(_DOCUMENT_EXTENSIONS):
        return True

    if any(marker in lowered_href.replace("%20", "") for marker in _LIST_MARKERS):
        return True

    # "weekly" plus a number is almost always a dated list page.
    if "weekly" in lowered_href and re.search(r"\d", lowered_href):
        return True

    # A title naming a list, with a number in it, from a planning path.
    if ("planning" in lowered_href
            and re.search(r"\blists?\b", lowered_title)
            and re.search(r"\d", f"{lowered_title} {lowered_href}")):
        return True

    return False


_REQUEST_TIMEOUT = 20
_USER_AGENT = "PlanPermitCompass/0.2 local source monitor"

# Words suggesting a document revises something already published, rather than
# adding something new. Reported differently, but still not interpreted.
_MATERIAL_HINTS = ("amend", "further information", "revised", "additional")

_MAX_REPORTED = 20


def index_documents(html: str, base_url: str) -> list[dict[str, str]]:
    """Index the planning-list documents linked from a weekly-list page.

    Returns `[{title, url}]`, de-duplicated by URL and resolved to absolute
    URLs so the stored baseline stays comparable across scans.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict[str, str]] = {}

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        title = anchor.get_text(" ", strip=True) or _FALLBACK_TITLE

        if not _looks_like_document(href, title):
            continue

        # A one- or two-character link text is a chevron or an icon, not a
        # document title worth alerting on.
        if len(title.strip()) < _MIN_TITLE_LENGTH and title != _FALLBACK_TITLE:
            continue

        url = urljoin(base_url, href)

        # Keyed by URL, so the same document linked twice is indexed once.
        found[url] = {"title": title[:_TITLE_LIMIT], "url": url}

    return list(found.values())


def fingerprint(documents: list[dict[str, str]]) -> str:
    """A stable digest of an index, for cheap "did anything change?" checks.

    Sorted before hashing so link reordering on the page is not a change.
    """
    joined = "|".join(sorted(f"{d['title']}::{d['url']}" for d in documents))
    return hashlib.sha256(joined.encode()).hexdigest()


def read_index(source_url: str) -> list[dict[str, str]]:
    """Fetch an official weekly-list page and index its documents."""
    response = requests.get(
        source_url,
        timeout=_REQUEST_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()
    return index_documents(response.text, source_url)


def compare(
    source: dict[str, str],
    current: list[dict[str, str]],
    previous: list[dict[str, str]] | None,
) -> list[dict[str, Any]]:
    """Compare an index against its baseline and describe what changed.

    Returns alerts shaped `{title, body, source_title, source_url, severity,
    alert_type}`. With no baseline, records one `baseline` alert and stops -
    the first scan establishes the comparison point, it does not report change.

    What this reports is strictly what is measurable: a document URL that is
    now in the index and was not before. It never claims the change is
    material, and never states a deadline.
    """
    alerts: list[dict[str, Any]] = []

    if previous is None:
        alerts.append({
            "title": "Watch baseline captured",
            "body": (
                f"Captured {len(current)} indexable planning-list documents from "
                "the official source. This is a baseline, not a planning "
                "conclusion; individual documents and deadlines need verification."
            ),
            "source_title": source["title"],
            "source_url": source["url"],
            "severity": "info",
            "alert_type": "baseline",
        })
        return alerts

    for document in current[:_MAX_REPORTED]:
        if any(document["url"] == earlier["url"] for earlier in previous):
            continue

        looks_material = any(
            hint in document["title"].lower() for hint in _MATERIAL_HINTS
        )

        if looks_material:
            title = "Potentially material source update"
            body = (
                f"The weekly-list index includes “{document['title']}”. "
                "It may relate to amended or additional material, but this local "
                "tool has not interpreted the document. Verify the primary source "
                "and deadline."
            )
        else:
            title = "New official planning-list document"
            body = (
                f"The weekly-list index includes “{document['title']}”. "
                "This detects a new published source document only; it is not a "
                "decision about a proposal or site."
            )

        alerts.append({
            "title": title,
            "body": body,
            "source_title": source["title"],
            "source_url": document["url"],
            "severity": "attention",
            "alert_type": "possible_material_change",
        })

    return alerts
