"""LangGraph StateGraph — routes to advisor or draft_review based on input.

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


class PlanningState(TypedDict, total=False):
    # Input
    lat: float
    lng: float
    radius_km: float
    construction_type: str
    site_condition: str
    question: str
    pdf_path: str
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
    # Meta
    errors: list[str]


def _route(state: PlanningState) -> str:
    if state.get("pdf_path"):
        return "draft_review"
    return "advisor"


_graph = StateGraph(PlanningState)
_graph.add_node("advisor", advisor_node)
_graph.add_node("draft_review", draft_review_node)
_graph.set_conditional_entry_point(_route)
_graph.add_edge("advisor", END)
_graph.add_edge("draft_review", END)

graph = _graph.compile()


def run(**kwargs: Any) -> dict[str, Any]:
    """Run the planning graph. Returns the final state dict."""
    initial: PlanningState = {"errors": [], **kwargs}  # type: ignore[typeddict-item]
    return graph.invoke(initial)
