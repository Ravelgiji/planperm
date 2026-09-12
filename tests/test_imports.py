"""Smoke test: every module imports without crashing.

This catches syntax errors, missing dependencies, and broken imports
BEFORE deployment. The indentation bug that took down production would
have been caught here.
"""

import unittest
import py_compile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ImportTests(unittest.TestCase):
    """Every Python file compiles and every agent module imports."""

    def test_all_python_files_compile(self):
        """Catches syntax errors, indentation bugs, etc."""
        errors = []
        for py_file in ROOT.rglob("*.py"):
            if ".venv" in str(py_file) or "__pycache__" in str(py_file) or "node_modules" in str(py_file):
                continue
            try:
                py_compile.compile(str(py_file), doraise=True)
            except py_compile.PyCompileError as e:
                errors.append(f"{py_file.relative_to(ROOT)}: {e}")
        self.assertEqual(errors, [], f"Compile errors:\n" + "\n".join(errors))

    def test_import_planning_data(self):
        from planning_data import fetch_nearby_applications, summarize_applications, normalize_decision

    def test_import_geocoder(self):
        from geocoder import resolve_location

    def test_import_helpers(self):
        from agents.helpers import (
            site_candidates, find_precedents, extract_web_text,
            resolve_authority, authority_sources, preparation_checklist,
        )

    def test_import_advisor(self):
        from agents.advisor import advisor_node

    def test_import_draft_review(self):
        from agents.draft_review import draft_review_node

    def test_import_orchestrator(self):
        from agents.orchestrator import semantic_router_node, specialist_route

    def test_import_guardrails(self):
        from agents.guardrails import sanitise_input, scrub_output, is_small_talk

    def test_import_graph(self):
        from agents.graph import graph, run

    def test_import_pdf_brief(self):
        from agents.pdf_brief import brief_to_pdf

    def test_graph_has_all_nodes(self):
        from agents.graph import graph
        node_names = set(graph.nodes.keys())
        expected = {"__start__", "advisor", "draft_review", "semantic_router",
                    "routed_advisor", "routed_draft", "watch", "coordinator", "clarify"}
        self.assertTrue(expected.issubset(node_names),
                        f"Missing nodes: {expected - node_names}")


if __name__ == "__main__":
    unittest.main()
