"""Answers to questions about the app itself.

The router has routes for draft, advisor and watch - all about planning. So
"which model are you using?" fell through to clarify and asked the user to
rephrase a question that was already perfectly clear.

These answers are assembled from real state rather than sent to a model: no
request is spent, nothing counts against a rate limit, and none of it can be
invented. Which matters most for the privacy answer, where a confident
hallucination would be a genuine problem.
"""

from __future__ import annotations

import os

# Ordered, because a question can brush more than one topic and the earlier
# entries are the more specific readings.
_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("model", (
        "which model", "what model", "which llm", "what llm",
        "large language model", "which ai", "what ai are you",
        "which engine", "which gpt", "what gpt", "same model",
    )),
    ("how_watch", (
        "how does the watch", "how does monitoring", "how do you detect",
        "how does the watch agent", "how does it detect", "how do you know what changed",
    )),
    ("privacy", (
        "is my data", "do you store", "where is my data", "are you tracking",
        "is this private", "do you share my", "who can see",
    )),
    ("capabilities", (
        "what can you do", "what do you do", "who are you", "what is this",
        "what are you", "how can you help", "what are your capabilities",
    )),
)


def detect_topic(prompt: str) -> str | None:
    lowered = (prompt or "").lower().strip()
    for topic, needles in _TOPICS:
        if any(needle in lowered for needle in needles):
            return topic
    return None


def _model_answer() -> str:
    from core import llm

    model = os.environ.get("PLANPERM_LLM_MODEL", "").strip() or llm.active_model()
    spent = [row["model"] for row in llm.status() if not row["available"]]

    parts = [
        f"Every agent on this page is using **{model}**, set with the Model "
        "control on the left. Changing it there moves the whole app - the "
        "router, the advisor, the draft reviewer and the watch agent all read "
        "the same setting.",
    ]

    if spent:
        parts.append(
            f"Rate-limited this session: {', '.join(spent)}. The daily "
            "allowance is counted per model, so switching is the way out."
        )

    parts.append(
        "The model writes the prose. It does not decide what changed or "
        "calculate a deadline - those are computed, and every figure links the "
        "published document it came from so you can check it."
    )
    return "\n\n".join(parts)


def _capabilities_answer() -> str:
    return (
        "Three things, and your question is routed to whichever fits.\n\n"
        "- **Area history** - what has been applied for near the pin, how much "
        "was granted, and what similar proposals did.\n"
        "- **Monitor change** - watches the planning authority's official "
        "weekly lists and reports newly published applications, each with an "
        "estimated observation deadline.\n"
        "- **Draft review** - upload an application PDF and it reads it back "
        "to you.\n\n"
        "Ask in your own words; you do not have to choose."
    )


def _privacy_answer() -> str:
    from core.watch_store import list_workspaces

    count = len(list_workspaces())
    return (
        f"Watched locations - {count} at the moment - and their alerts are "
        "written to this machine only, under `watch_data/`. Nothing is "
        "uploaded, nothing is emailed, and nothing is shared between watched "
        "areas.\n\n"
        "Scans run only when you press the button; the app never polls in the "
        "background. Your question and the published planning records for the "
        "selected site are sent to the language model to compose an answer - "
        "that is the one thing that leaves the machine."
    )


def _how_watch_answer() -> str:
    return (
        "It takes a snapshot of the planning authority's official weekly-list "
        "pages and stores what was published there. On a later scan it "
        "compares the two: any document present now that was not in the "
        "snapshot is a change.\n\n"
        "That comparison is arithmetic over a set of URLs rather than a "
        "judgement - you can open the stored snapshot and check it. When a "
        "newly published list of received applications appears, it reads the "
        "applications out of the document and counts five weeks from the "
        "receipt date printed there to estimate each observation deadline.\n\n"
        "Every deadline is labelled an estimate and links its source, because "
        "the council's own file is the authority on timing - not the "
        "subtraction."
    )


_ANSWERS = {
    "model": _model_answer,
    "capabilities": _capabilities_answer,
    "privacy": _privacy_answer,
    "how_watch": _how_watch_answer,
}


def answer(prompt: str) -> str | None:
    """An answer about the app, or None when the question is not about it."""
    topic = detect_topic(prompt)
    if topic is None:
        return None
    return _ANSWERS[topic]()
