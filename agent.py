"""PlanPerm LLM Agent — function-calling agent grounded in real planning data."""

import json
from openai import OpenAI
from config import LLM_API_KEY, LLM_MODEL, LLM_BASE_URL
from analysis import (
    compute_stats,
    find_precedents,
    format_stats_text,
    compute_timeline_stats,
    compute_appeal_stats,
)

# ── System prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are PlanPerm, a planning permission analyst for Ireland.

You help users understand planning application patterns in their area using
real data from MyPlan.ie. You answer questions about approval rates, common
refusal reasons, precedents for specific development types, timelines, and
appeal outcomes.

RULES:
- You ONLY use data from the tools provided. Never invent statistics or applications.
- When you cite a statistic, mention how many applications it's based on.
- When you reference a specific application, include its reference number.
- You NEVER give legal advice. You are informational only.
- If the user asks for legal advice, say: "I can't give legal advice — consult a planning professional."
- If the data is sparse (fewer than 10 decided applications), warn the user that patterns may not be reliable.
- Keep answers concise. Lead with the key finding, then supporting detail.
- End every answer with: "⚠️ Informational only — not legal or professional planning advice."
"""

# ── Tool definitions (OpenAI function-calling schema) ────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_area_stats",
            "description": "Get summary statistics for planning applications in the current area: total count, approval rate, breakdown by decision, approval rate by application type.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_similar_applications",
            "description": "Find planning applications in the area that match a description. Use this when the user asks about chances for a specific type of development (e.g. 'rear extension', 'dormer', 'new dwelling').",
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords describing the development type to search for, e.g. ['extension', 'rear', 'two-storey']",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of precedents to return. Default 10.",
                        "default": 10,
                    },
                },
                "required": ["keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_timeline_stats",
            "description": "Get statistics about decision timelines: average days to decision, approval rate by year, and recent trend.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_appeal_stats",
            "description": "Get statistics about planning appeals in the area: how many refusals were appealed, how many appeals succeeded.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_application_types",
            "description": "Compare approval rates across different application types (Permission, Retention, Outline Permission, etc.).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# ── Tool execution ───────────────────────────────────────────────────────────


def _execute_tool(name: str, arguments: dict, apps: list[dict], stats: dict) -> str:
    """Execute a tool call and return the result as a string."""

    if name == "get_area_stats":
        return format_stats_text(stats)

    if name == "find_similar_applications":
        keywords = arguments.get("keywords", [])
        limit = arguments.get("limit", 10)
        precedents = find_precedents(apps, keywords, limit=limit)

        if not precedents:
            return f"No applications found matching: {', '.join(keywords)}"

        granted = sum(1 for p in precedents if p["decision"] == "GRANTED")
        refused = sum(1 for p in precedents if p["decision"] == "REFUSED")
        pending = sum(1 for p in precedents if p["decision"] == "PENDING")

        lines = [
            f"Found {len(precedents)} similar applications ({granted} granted, {refused} refused, {pending} pending):",
            "",
        ]
        for p in precedents:
            date_str = p.get("date_decided") or p.get("date_received") or "no date"
            link = p.get("link", "")
            lines.append(
                f"- **{p['application_ref']}** ({date_str}) — {p['description'][:100]} "
                f"→ **{p['decision']}**"
                + (f" [{p['council']}]" if p.get("council") else "")
            )
        return "\n".join(lines)

    if name == "get_timeline_stats":
        ts = compute_timeline_stats(apps)
        if not ts:
            return "Not enough decided applications to compute timeline statistics."

        lines = []
        if ts.get("avg_days") is not None:
            lines.append(f"Average time to decision: **{ts['avg_days']} days** ({round(ts['avg_days']/7, 1)} weeks)")
        if ts.get("median_days") is not None:
            lines.append(f"Median time to decision: **{ts['median_days']} days**")

        if ts.get("by_year"):
            lines.append("\nApproval rate by year:")
            for year, rate in sorted(ts["by_year"].items()):
                lines.append(f"- {year}: {rate}%")

        if ts.get("trend"):
            lines.append(f"\nTrend: {ts['trend']}")

        return "\n".join(lines) if lines else "No timeline data available."

    if name == "get_appeal_stats":
        ap = compute_appeal_stats(apps)
        if not ap or ap.get("total_refused", 0) == 0:
            return "No refusal or appeal data available for this area."

        lines = [
            f"Total refused: **{ap['total_refused']}**",
            f"Appeals filed: **{ap['appeals_filed']}** ({ap['appeal_rate']}% of refusals)",
        ]
        if ap["appeals_filed"] > 0:
            lines.append(f"Appeals successful: **{ap['appeals_granted']}** ({ap['appeal_success_rate']}%)")
        return "\n".join(lines)

    if name == "compare_application_types":
        by_type = stats.get("approval_by_type", {})
        if not by_type:
            return "Not enough data to compare application types."

        lines = ["Approval rate by application type:"]
        for atype, rate in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- {atype}: **{rate}%**")
        return "\n".join(lines)

    return f"Unknown tool: {name}"


# ── Agent runner ─────────────────────────────────────────────────────────────


def create_client() -> OpenAI | None:
    """Create an OpenAI client. Returns None if no API key is configured."""
    if not LLM_API_KEY:
        return None
    kwargs = {"api_key": LLM_API_KEY}
    if LLM_BASE_URL:
        kwargs["base_url"] = LLM_BASE_URL
    return OpenAI(**kwargs)


def run_agent(
    user_message: str,
    apps: list[dict],
    stats: dict,
    chat_history: list[dict] | None = None,
) -> str:
    """Run the agent with function-calling. Returns the assistant's response.

    Falls back to rule-based answers if no LLM API key is configured.
    """
    client = create_client()
    if client is None:
        return _fallback_answer(user_message, apps, stats)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add chat history (keep last 10 exchanges to stay within context)
    if chat_history:
        messages.extend(chat_history[-20:])

    messages.append({"role": "user", "content": user_message})

    # Allow up to 3 rounds of tool calls
    for _ in range(3):
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=1000,
        )

        choice = response.choices[0]

        if choice.finish_reason == "stop":
            return choice.message.content or ""

        if choice.finish_reason == "tool_calls" or choice.message.tool_calls:
            # Append the assistant message with tool calls
            messages.append(choice.message)

            for tool_call in choice.message.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                result = _execute_tool(fn_name, fn_args, apps, stats)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })
            continue

        # Unexpected finish reason — return whatever we got
        return choice.message.content or ""

    return "I wasn't able to fully process that question. Try rephrasing it."


def _fallback_answer(question: str, apps: list[dict], stats: dict) -> str:
    """Rule-based fallback when no LLM is available.

    This preserves the PoC keyword-matching behaviour so the app works
    without an API key, just less intelligently.
    """
    q = question.lower()
    total = stats.get("total", 0)
    rate = stats.get("approval_rate")

    keywords = [w for w in q.split() if len(w) > 3 and w not in {
        "what", "would", "could", "should", "about", "there", "these",
        "have", "been", "with", "from", "this", "that", "will", "does",
    }]

    precedents = find_precedents(apps, keywords) if keywords else []

    if any(w in q for w in ["chance", "likely", "probability", "approved", "get permission"]):
        granted_prec = [p for p in precedents if p["decision"] == "GRANTED"]
        refused_prec = [p for p in precedents if p["decision"] == "REFUSED"]

        lines = []
        if rate is not None:
            lines.append(f"The overall approval rate in this area is **{rate}%** across {total} applications.")

        if precedents:
            lines.append(
                f"\nI found **{len(precedents)}** similar applications: "
                f"{len(granted_prec)} granted, {len(refused_prec)} refused."
            )
        else:
            lines.append("\nNo closely matching precedents found — try being more specific.")

        lines.append("\n\n*⚠️ Informational only — not legal or professional planning advice.*")
        return "\n".join(lines)

    if any(w in q for w in ["refused", "refusal", "reject", "denied", "why"]):
        top = stats.get("top_refusal_reasons", [])
        if top:
            lines = ["**Top refusal reasons in this area:**"]
            for reason, count in top:
                lines.append(f"- {reason} ({count} applications)")
            lines.append("\n*⚠️ Informational only — not legal or professional planning advice.*")
            return "\n".join(lines)
        return "No refusals recorded in this area."

    if any(w in q for w in ["condition", "conditions", "attached", "requirements"]):
        top = stats.get("top_conditions", [])
        if top:
            lines = ["**Common conditions attached to grants:**"]
            for cond, count in top:
                lines.append(f"- {cond} ({count} applications)")
            lines.append("\n*⚠️ Informational only — not legal or professional planning advice.*")
            return "\n".join(lines)
        return "No condition data available."

    if any(w in q for w in ["timeline", "long", "weeks", "days", "time", "wait"]):
        ts = compute_timeline_stats(apps)
        if ts and ts.get("avg_days"):
            return (
                f"Average time to decision: **{ts['avg_days']} days** "
                f"({round(ts['avg_days']/7, 1)} weeks)\n\n"
                "*⚠️ Informational only — not legal or professional planning advice.*"
            )
        return "Not enough data to compute timeline statistics."

    if any(w in q for w in ["appeal", "appeals", "overturned"]):
        ap = compute_appeal_stats(apps)
        if ap and ap.get("appeals_filed", 0) > 0:
            return (
                f"Of **{ap['total_refused']}** refusals, **{ap['appeals_filed']}** were appealed "
                f"({ap['appeal_rate']}%). Of those, **{ap['appeals_granted']}** succeeded "
                f"({ap['appeal_success_rate']}%).\n\n"
                "*⚠️ Informational only — not legal or professional planning advice.*"
            )
        return "No appeal data available for this area."

    if any(w in q for w in ["type", "types", "breakdown", "categories"]):
        by_type = stats.get("approval_by_type", {})
        if by_type:
            lines = ["**Approval rate by application type:**"]
            for atype, arate in by_type.items():
                lines.append(f"- {atype}: {arate}%")
            lines.append("\n*⚠️ Informational only — not legal or professional planning advice.*")
            return "\n".join(lines)
        return "Not enough data to break down by type."

    if precedents:
        lines = [f"Here are **{len(precedents)}** relevant applications:"]
        for p in precedents:
            lines.append(f"- **{p['application_ref']}** — {p['description'][:80]} → **{p['decision']}**")
        lines.append("\n*⚠️ Informational only — not legal or professional planning advice.*")
        return "\n".join(lines)

    return (
        f"I have data on **{total}** applications in this area"
        + (f" with an approval rate of **{rate}%**." if rate else ".")
        + "\n\nTry asking about:\n"
        "- Your chances for a specific type of development\n"
        "- Common refusal reasons\n"
        "- How long decisions take\n"
        "- Appeal success rates\n"
        "- Approval rates by application type\n\n"
        "*⚠️ Informational only — not legal or professional planning advice.*"
    )
