"""Semantic routing nodes for the existing PlanPerm LangGraph workflow.

This module classifies the requested specialist with OpenAI structured output and
delegates to the existing specialist implementations without modifying them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agents.advisor import advisor_node as existing_advisor_node
from agents.draft_review import draft_review_node as existing_draft_node

_ROUTES = {"draft", "advisor", "watch", "coordinator", "clarify"}
_CONFIDENCE = {"high", "medium", "low"}
_PROJECT_ROOT = Path(__file__).resolve().parents[1]

ROUTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["draft", "advisor", "watch", "coordinator", "clarify"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "rationale": {"type": "string"},
        "missing_context": {"type": "array", "items": {"type": "string"}},
        "split_task": {"type": "boolean"},
        "secondary_route": {"type": "string", "enum": ["draft", "advisor", "watch", "coordinator", "clarify", "none"]},
    },
    "required": ["route", "confidence", "rationale", "missing_context", "split_task", "secondary_route"],
    "additionalProperties": False,
}


def _fallback(query: str) -> dict[str, Any]:
    """Keep the app usable if Manus routing is unavailable; this is not semantic routing."""

    lowered = query.lower()
    signals = {
        "draft": ("review my pdf", "review my draft", "check my application"),
        "advisor": ("advice", "advise", "permission", "exempt", "process", "appeal", "policy", "requirement",
                     "generate", "brief", "guide", "prepare", "checklist", "what do i need", "draft for me"),
        "watch": ("watch", "monitor", "alert", "notify", "track", "weekly", "monthly", "what changed", "anything new"),
        "coordinator": ("presentation", "research", "briefing", "synthesise", "synthesize"),
    }
    matches = [route for route, words in signals.items() if any(word in lowered for word in words)]
    if len(matches) > 1:
        return {
            "route": "clarify",
            "confidence": "low",
            "rationale": "Manus semantic routing is unavailable and the fallback cannot safely identify one specialist.",
            "missing_context": ["the one outcome you want first"],
            "split_task": len(matches) > 1,
            "secondary_route": matches[1] if len(matches) > 1 else "none",
            "method": "rule_fallback",
        }
    return {
        "route": matches[0] if matches else "advisor",
        "confidence": "medium",
        "rationale": "Manus semantic routing is unavailable, so a transparent local fallback sent this site question to the Advisor Agent.",
        "missing_context": [],
        "split_task": False,
        "secondary_route": "none",
        "method": "rule_fallback",
    }


def _validate(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("route") not in _ROUTES or value.get("confidence") not in _CONFIDENCE:
        return None
    if not isinstance(value.get("rationale"), str) or not isinstance(value.get("missing_context"), list):
        return None
    if not all(isinstance(item, str) for item in value["missing_context"]):
        return None
    if not isinstance(value.get("split_task"), bool):
        return None
    if value.get("secondary_route") not in {*_ROUTES, "none"}:
        return None
    return value


def _recent_turns(state: dict[str, Any], limit: int = 6) -> str:
    """The last few turns, for resolving a follow-up against what preceded it."""
    history = state.get("chat_history") or []
    if not history:
        return "none - this is the first question"

    lines = []
    for message in history[-limit:]:
        speaker = "User" if message.get("role") == "user" else "Assistant"
        text = " ".join(str(message.get("content") or "").split())[:240]
        if text:
            lines.append(f"{speaker}: {text}")

    return "\n".join(lines) or "none"


def _semantic_prompt(query: str, state: dict[str, Any]) -> str:
    return f"""You route planning queries for PlanPerm. Classify only the user's primary outcome; do not answer the planning question.

Routes:
- draft: review an existing draft document or prepare application wording.
- watch: ONLY what is NEW since the user's last scan - newly published
  weekly-list documents, and observation deadlines on applications in them.
  "What changed?", "anything new?", "any new applications this week?",
  "what are the deadlines?" belong here.
  The Watch Agent knows nothing about applications published before the last
  scan, so it CANNOT answer what exists at a place. Never send a question about
  existing or historic applications here.
- advisor: everything about what ALREADY EXISTS or is typical - "are there any
  applications at X?", "what has been applied for near here?", "how many
  applications / what is the approval rate?", "has anything been refused
  nearby?", "what do I need to apply?", requirements, process, policy,
  precedents. Questions naming a place and asking what is registered,
  submitted, granted or refused there are ADVISOR questions.
- coordinator: presentation, research synthesis or combining agent outputs.
- clarify: unclear, low confidence or multiple independent outcomes.

The selected site and nearby records are sufficient context for a short query such as "advise me on this"; send it to advisor, not clarify.

The current Draft Agent reviews an already uploaded PDF only. Choose draft only when `pdf_uploaded` is true. If a user requests document drafting or review with no uploaded PDF, choose clarify and request an uploaded PDF. IMPORTANT: if a user asks to "generate a draft", "create a brief", "give me a guide", or "prepare a summary", this is an ADVISOR task, not a draft review — the Advisor generates preparation briefs. Only route to draft when the user explicitly wants to review an existing uploaded PDF. Set split_task=true and route=clarify when a user asks for independent outcomes, for example permission advice plus a separate document review. A Watch Agent may be connected separately.

Recent conversation (use it to resolve a short follow-up - "20 kms?" after a
question about applications near a place is still that same question, with a
different radius, and routes where the earlier question routed; only choose
clarify when the history genuinely does not settle it):
{_recent_turns(state)}

User query: {query}
Safe context: site={state.get('site_label') or f"{state.get('lat', 'unknown')},{state.get('lng', 'unknown')}"}; radius_km={state.get('radius_km', 'not supplied')}; nearby_record_count={len(state.get('records') or [])}; pdf_uploaded={bool(state.get('pdf_path'))}; watched_area_saved={bool(state.get('workspace_id'))}"""


def classify_with_llm(query: str, state: dict[str, Any]) -> dict[str, Any] | None:
    """Classify the query route using OpenAI structured output. Returns validated route or None."""

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from openai import OpenAI

        base_url = os.getenv("PLANPERM_LLM_BASE_URL")
        model = os.getenv("PLANPERM_LLM_MODEL", "gpt-4.1-mini")
        client = OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": """You route planning queries. Respond ONLY with valid JSON matching this exact schema:
{"route": "draft"|"advisor"|"watch"|"coordinator"|"clarify", "confidence": "high"|"medium"|"low", "rationale": "why this route", "missing_context": ["list of missing items or empty"], "split_task": true|false, "secondary_route": "draft"|"advisor"|"watch"|"coordinator"|"clarify"|"none"}

Do not answer the planning question. Only classify the route."""},
                {"role": "user", "content": _semantic_prompt(query, state)},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=250,
        )
        raw = response.choices[0].message.content or ""
        parsed = json.loads(raw)
        return _validate(parsed)
    except Exception:
        return None


def semantic_router_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node: classify one route, but do not perform specialist work."""

    raw_question = str(state.get("question") or "")
    from agents.guardrails import sanitise_input
    cleaned, blocked = sanitise_input(raw_question)
    if blocked:
        return {"orchestrator": {"route": "clarify", "confidence": "high", "rationale": "Input blocked by guardrails.", "missing_context": [], "split_task": False, "secondary_route": "none", "method": "guardrail"}, "response": cleaned}

    decision = classify_with_llm(cleaned, state)
    decision = {**decision, "method": "openai_structured"} if decision else _fallback(cleaned)
    if decision["route"] == "draft" and not state.get("pdf_path"):
        decision = {
            **decision,
            "route": "clarify",
            "confidence": "low",
            "rationale": "The existing Draft Review Agent can only review an uploaded PDF.",
            "missing_context": ["a PDF application draft uploaded through the Draft Review control"],
            "split_task": False,
            "secondary_route": "none",
        }
    elif decision["confidence"] == "low" or decision["split_task"]:
        # Low-confidence advisor is still better than clarify — the advisor
        # handles vague questions well. Only bounce non-advisor low-confidence.
        if decision["route"] != "advisor":
            decision["route"] = "clarify"
    return {"orchestrator": decision}


def specialist_route(state: dict[str, Any]) -> str:
    route = str((state.get("orchestrator") or {}).get("route", "clarify"))
    return route if route in _ROUTES else "clarify"


def advisor_agent_node(state: dict[str, Any]) -> dict[str, Any]:
    """Delegate unchanged to the existing UI branch Advisor Agent."""

    updated = existing_advisor_node({**state, "errors": list(state.get("errors", []))})
    return {**updated, "response": updated.get("advice", "Advisor Agent did not return advice.")}


def draft_agent_node(state: dict[str, Any]) -> dict[str, Any]:
    """Delegate unchanged to the existing Draft Review Agent when a PDF is supplied."""

    if not state.get("pdf_path"):
        return {"response": "The existing Draft Review Agent needs an application PDF to review. Upload or provide a draft document, then submit the review request."}
    updated = existing_draft_node({**state, "errors": list(state.get("errors", []))})
    return {**updated, "response": updated.get("draft_review", "Draft Review Agent did not return a review.")}


def watch_agent_node(state: dict[str, Any]) -> dict[str, Any]:
    """Delegate to a Watch Agent if the collaborator module has been added."""

    try:
        from agents.watch import watch_node
    except ImportError:
        return {"response": "The Watch Agent is not connected in this UI branch yet. Its module can be added later as `agents/watch.py` without changing the Orchestrator route."}
    updated = watch_node(state)
    return {**updated, "response": updated.get("watch_response", "Watch Agent did not return a response.")}


def coordinator_node(_state: dict[str, Any]) -> dict[str, Any]:
    return {"response": "This is a presentation or research-synthesis request, so it remains with the Orchestrator."}


def clarify_node(state: dict[str, Any]) -> dict[str, Any]:
    decision = state.get("orchestrator") or {}
    if decision.get("split_task"):
        return {"response": "This request contains more than one independent outcome. Please submit the first outcome separately so it can be routed safely."}
    missing = ", ".join(decision.get("missing_context") or ["the main outcome you want"])
    return {"response": f"Please clarify {missing}. I can route one request to Draft, Advisor or Watch."}
