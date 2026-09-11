# PlanPerm UI Refresh — Design Exploration

## Three candidate directions

### 1. Cartographer’s Desk
**Very Brief Intro:** A contemporary civic-intelligence workspace that pairs the legibility of a planning atlas with a calm professional product experience. It makes complex local planning evidence feel inspectable rather than intimidating.

**Probability:** 0.06

### 2. Irish Modernist Gazette
**Very Brief Intro:** An editorial, typographic interface inspired by public records, archive stamps, and Irish wayfinding. It would foreground document credibility and precedent research through strong grids and print-like hierarchy.

**Probability:** 0.03

### 3. Signal over City
**Very Brief Intro:** A dark analytical command centre with low-light mapping, high-signal data marks, and restrained luminous accents. It would feel like a specialist assessment tool used by planning professionals.

**Probability:** 0.08

---

## Chosen direction — Cartographer’s Desk

### Design Movement
**New civic modernism**: practical, warm, geographic, and evidence-led. The interface should suggest a well-organised planning studio rather than a generic software dashboard.

### Core Principles
1. **Evidence before ornament:** The map, planning status, confidence indicators, and source details remain immediately scannable.
2. **One calm field of focus:** A wide analysis canvas is paired with a narrow, anchored intelligence rail; users can understand location context and decision reasoning together.
3. **Progressive disclosure:** Dense planning details appear in composed cards, drawers, and tabbed sections rather than as an overwhelming wall of controls.
4. **Human, not bureaucratic:** Warm off-whites, generous spacing, familiar language, and precise microcopy make planning research approachable.

### Color Philosophy
The base is a lightly warm map-paper white, so planning data feels trustworthy and easy to read for extended work. Deep atlantic navy anchors primary navigation and factual UI; **planning green** communicates positive permission signals without looking promotional; muted clay identifies risk or refusal context. Colour is reserved for meaning—never used as decoration alone.

### Layout Paradigm
The main page is a **split terrain**: an asymmetric analysis workbench with a full-height map-like canvas on the left and a structured insights column on the right. A compact top bar controls place and scope, while a floating command layer provides search and analysis actions. On small screens, the insight rail becomes a clear sequence beneath the analysis summary, with the map retained as the primary visual evidence.

### Signature Elements
1. **Contour-line field:** Subtle, non-distracting topographic curves appear in backgrounds and map framing to tie the product to place.
2. **Evidence pins:** Planning status uses softly dimensional, high-contrast pins with a distinct inner mark, not anonymous map dots.
3. **Field notes:** Short, attributed insight callouts use a left-rule and source labels to make AI conclusions feel grounded in data.

### Interaction Philosophy
Interactions should feel like handling a well-designed map: controlled, tactile, and direct. Cards elevate slightly on hover; filters have clear on/off states; map pins reveal concise evidence cards before expanding into full case details. The AI prompt is framed as a research question, with suggested prompts that adapt to the selected place.

### Animation
Use short, grounded transitions with `cubic-bezier(0.23, 1, 0.32, 1)`. Map markers and analysis panels enter with a small upward offset and staggered opacity; drawers slide in from their originating edge; buttons compress to `scale(0.97)` on activation. Keep all routine interaction motion under 220ms and honor `prefers-reduced-motion` by removing non-essential transitions.

### Typography System
Use **DM Sans** for interface labels, tables, and controls because it remains crisp at compact sizes. Pair it with **Fraunces** for critical summaries and prominent location questions, giving the product the authority of a researched report without becoming decorative. Labels use medium-weight, slightly tracked uppercase only for provenance and compact metadata; insight headlines remain sentence case and broadly readable.

### Brand Essence
**PlanPerm turns Irish planning records into a clear, local argument for homeowners, architects, and property professionals.**

**Personality:** Grounded, incisive, reassuring.

### Brand Voice
Headlines speak with measured confidence and useful specificity; CTAs invite investigation rather than push a conversion. Microcopy names what the interface knows, what it estimates, and where the evidence came from.

> “See how nearby decisions shape your proposal.”

> “Ask a planning question, grounded in this area’s record.”

### Wordmark & Logo
The wordmark uses a softened-but-precise sans word shape beside a **folded contour-and-permission tick**: a green, abstract map fold with a small cut-through approval notch. The symbol works on its own as a favicon and map loading mark; it contains no text.

### Signature Brand Color
**Permission Green — `#0A7666`**. A deep, distinctive teal-green that signals approval, local landscape, and quiet confidence without mimicking generic finance or healthcare palettes.

## Style Decisions

- Permission Green is semantic rather than decorative: it identifies approval and permission outcomes, active investigative controls, evidence confidence, and the primary question pathway.
- Each major section must visibly connect to the active evidence workbench through a location, a planning precedent, a source, a confidence signal, or a research question.
- The wordmark uses a softened, precise sans expression paired with the folded contour-and-permission-tick symbol. Editorial serif typography is reserved for researched summaries, not the brand lockup.

## Streamlit Refinement — Dual-Theme Atlas

The Streamlit version will keep its small functional footprint while gaining the composure of a premium property-planning product. A single panoramic Irish terrain image will be used as an atmospheric header field with an obscured text-safe area; it supports the workspace without adding a marketing section or distracting from the map.

The layout will retain the analysis triad—search controls, map evidence, assistant—but introduce a stronger top navigation bar, deliberate panel headers, compact source badges, and a calmer hierarchy. The **light theme** uses map-paper, field green, and ink. The **dark theme** becomes a true low-light planning workspace: blue-black surfaces, desaturated controls, pale text, and restrained teal/orange evidence marks.

The theme toggle will be an explicit Streamlit control that persists in session state. It will restyle the page background, surfaces, typography, controls, metric cards, chat panel, and map frame. The underlying map remains familiar and readable in both modes.
