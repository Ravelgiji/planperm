"""LangGraph StateGraph — routes planning queries to existing specialists.

Usage:
    from agents.graph import run
    result = run(lat=53.27, lng=-9.05, construction_type="New dwelling")
    result = run(lat=53.27, lng=-9.05, pdf_path="/tmp/draft.pdf")
    result = run(lat=53.27, lng=-9.05, radius_km=3.0, construction_type="Extension")
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agents.advisor import advisor_node
from agents.draft_review import draft_review_node
from agents.orchestrator import (
    advisor_agent_node,
    clarify_node,
    coordinator_node,
    draft_agent_node,
    semantic_router_node,
    specialist_route,
    watch_agent_node,
)


class PlanningState(TypedDict, total=False):
    # Input
    lat: float
    lng: float
    radius_km: float
    construction_type: str
    site_condition: str
    question: str
    pdf_path: str
    site_label: str
    # Resolved
    jurisdiction: str
    authority: str
    authority_resolution: dict[str, str]
    # Research
    records: list[dict[str, Any]]
    summary: dict[str, Any]
    candidates: list[dict[str, Any]]
    precedents: list[dict[str, Any]]
    sources: list[dict[str, str]]
    evidence_text: str
    checklist: list[dict[str, str]]
    # Outputs
    advice: str
    draft_text: str
    draft_review: str
    response: str
    orchestrator: dict[str, Any]
    # Meta
    errors: list[str]


def _route(state: PlanningState) -> str:
    if state.get("question"):
        return "semantic_router"
    if state.get("pdf_path"):
        return "draft_review"
    return "advisor"


_graph = StateGraph(PlanningState)
_graph.add_node("advisor", advisor_node)
_graph.add_node("draft_review", draft_review_node)
_graph.add_node("semantic_router", semantic_router_node)
_graph.add_node("routed_advisor", advisor_agent_node)
_graph.add_node("routed_draft", draft_agent_node)
_graph.add_node("watch", watch_agent_node)
_graph.add_node("coordinator", coordinator_node)
_graph.add_node("clarify", clarify_node)
_graph.set_conditional_entry_point(_route)
_graph.add_conditional_edges(
    "semantic_router",
    specialist_route,
    {
        "advisor": "routed_advisor",
        "draft": "routed_draft",
        "watch": "watch",
        "coordinator": "coordinator",
        "clarify": "clarify",
    },
)
_graph.add_edge("advisor", END)
_graph.add_edge("draft_review", END)
_graph.add_edge("routed_advisor", END)
_graph.add_edge("routed_draft", END)
_graph.add_edge("watch", END)
_graph.add_edge("coordinator", END)
_graph.add_edge("clarify", END)

graph = _graph.compile()


def run(**kwargs: Any) -> dict[str, Any]:
    """Run the planning graph. Returns the final state dict."""
    initial: PlanningState = {"errors": [], **kwargs}  # type: ignore[typeddict-item]
    return graph.invoke(initial)
