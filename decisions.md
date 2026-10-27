# Project Decisions Log

## Decision 1: Schema will be self-designed

I will design the event schema myself rather than adopting an off-the-shelf schema or letting it be auto-derived from the raw dataset. Until this schema exists, advanced journey insights cannot be reliably extracted.

### Context / reference discussion

The insight I want the schema to support (example):

> Before product X was purchased, the customer visited product Y, Z, A, then viewed X three times, within X clicked "show product features" (spent time reading them), clicked on reviews before X was purchased.

This requires events at two levels:

**1. Cross-product sequence events (the "Y, Z, A before X" part)**
- `view_product` — needs to repeat, and needs a `product_id` on every row so I can count "viewed X three times" and reconstruct the exact order of Y → Z → A → X.
- This already exists in a basic form in the raw dataset, but the insight only works if every view event is tied to a product AND order/timestamp is preserved precisely enough to sequence multiple products before a purchase.

**2. Within-product-page micro-events (the "clicked show features, spent time reading, clicked reviews" part)**
- `expand_product_features` (or `click_show_features`) — paired with a computed `time_spent_on_features`, derived from the gap to the next event rather than logged directly.
- `view_reviews` — kept separate from `view_product`, since I specifically want to know whether reviews were checked before buying.
- Possibly `expand_review` / `scroll_reviews` if more granularity is needed than just "opened the reviews tab."

**Structural implication:** "spent X time reading" is not a separate event to log — it's a derived metric computed after the fact (`time_to_next_event`). The event taxonomy needs `expand_product_features` to exist as a click; duration is calculated afterward, not logged directly. Some of what the insight describes is an event; some of it is a derived metric computed post-hoc. Don't log "time spent" as its own event — log the click, derive the time.

**Proposed event set (initial draft, pending my own schema work):**

*Cross-product / journey-level*
- `view_product` (repeatable, tied to `product_id` — lets me count "viewed 3 times")
- `view_similar_products` / `view_recommendations` (captures whether alternatives were browsed)

*In-page engagement (micro-behavior within one product)*
- `expand_product_features`
- `view_reviews`
- `expand_review` (opened a specific review, if granular enough detail is wanted)
- `zoom_product_image`
- `watch_product_video`

*Intent / conversion*
- `add_to_cart`
- `remove_from_cart`
- `purchase`

**Backbone requirement:** to reconstruct "Y, Z, A then X three times, then features, then reviews, then purchase" as one clean story, every event needs: `user_id`, `session_id` (or a persistent `journey_id` spanning multiple sessions if the decision took days), `product_id`, `event_type`, precise `timestamp`. The event *names* matter less than ensuring every single one carries these five fields consistently.

### Follow-up refinement (search-depth / scroll-depth example)

For finer-grained behavior — e.g., search depth, scroll depth, and time spent at each scroll position — the base event table will NOT carry these columns directly (this would leave most columns null/0 for most rows, since a purchase event has no scroll depth, a search event has no review-expansion, etc.).

Instead, the model will be:
- **Core/base event table** — one row per event, consistent columns: `event_id`, `user_id`, `session_id`, `event_type`, `product_id`, `timestamp`. This is what feeds PM4Py, Sankeys, funnels, etc.
- **Satellite / micro-event tables** — one table per rich event type needing extra structured detail, linked back via `event_id` or `session_id`. E.g.:
  - `search_events`: `event_id`, `query_text`, `results_returned`, `result_position_clicked`, `scroll_depth_pct`, `time_at_depth`
  - `product_page_engagement`: `event_id`, `product_id`, `feature_expanded`, `time_spent_seconds`, `reviews_opened`, `scroll_checkpoints`

This is the standard pattern used by real analytics systems (Amplitude, Segment, Snowplow) — a thin, standardized event spine, with rich structured payloads per event type where needed.

**Open granularity question (to resolve once the schema draft exists):** whether scroll behavior should be modeled as one row per scroll checkpoint (more granular, supports a scroll-depth-vs-time visualization) or one row per session with a nested array/JSON column of checkpoints (more compact, but each checkpoint can't be treated as its own discoverable "step" by tools like PM4Py). Given the stated goal (time spent at a given scroll depth), one-row-per-checkpoint is the likely direction — to be confirmed once the schema is drafted.

### Schema implication from segment profile feature (Decision 8)

Categorical fields (state, gender, device type, etc.) are **user/customer attributes, not events** — they require a new **`users` dimension table**, separate from the event fact table, joined via `user_id`. This is a fact table + dimension table pattern (event fact table = what happened; user dimension table = who they are), consistent with the fact/satellite structure above.

### Status

Schema is finalized (9 tables total):

**Table: Customer Attributes (static)**
- `user_id`, `customer_name`, `age`, `city`, `country`, `gender`, `membership_status`, `sign_up_date`

**Table: Customer Behavioral Metrics (derived/computed)**
*Computed live from the event tables — not manually maintained, but a real defined table in the schema.*
- `user_id`, `total_sessions`, `login_frequency`, `page_visits`, `reviews_given`, `discount_usage`, `session_duration_average`, `pages_per_session`, `cart_abandonment_rate`, `wishlist_items`, `total_purchases`, `average_order_value`, `days_since_last_purchase`, `email_open_rate`, `customer_service_calls`, `payment_mode_diversity`, `lifetime_value`

**Table: Products**
- `product_id`, `product_name`, `category_id`, `category_code`, `brand`, `current_price`

**Table: Transactional & Session Events**
- `event_id`, `event_time`, `event_type` (`add_to_cart`, `remove_from_cart`, `purchase`, `apply_coupon`, `login`), `product_id`, `price` (price at time of event, may differ from Products.current_price), `user_id`, `user_session`, `journey_id` (links sessions together into one multi-day customer decision arc)

**Table: Search & Filter Events**
- `event_id`, `event_time`, `event_type` (`search_query`, `apply_filter`, `scroll_checkpoint`), `search_term`, `search_filter_1`, `search_filter_2`, `search_filter_3`, `scroll_position` (raw checkpoint value; depth/time-at-depth derived afterward from consecutive rows), `user_id`, `user_session`, `journey_id`

**Table: Product Page Engagement Events**
- `event_id`, `event_time`, `event_type` (`view_product`, `view_reviews`, `view_comparison`, `zoom_image`, `expand_description`, `check_contents`, `watch_video`, `swipe_pictures`), `product_id`, `session_id`, `user_id`, `journey_id`

**Table: Search Events — Satellite**
- `event_id` (FK to Search & Filter Events), `results_returned`, `result_position_clicked`

**Table: Product Page Engagement — Satellite**
- `event_id` (FK to Product Page Engagement Events), `time_spent_seconds` (derived from gap to next event)

**Table: Starred Paths**
- `pm_user_id` (the product manager, not a customer), `path_id` (variant/sequence identifier from Layer 2), `starred_at`

Note: `login` is placed in Transactional & Session Events since, in e-commerce, login commonly happens at checkout rather than as a separate session-lifecycle step.

---

## Decision 2: Use PM4Py to create 2 layers in the dashboard

**Layer 1:** the process diagram itself.
**Layer 2:** a table containing the top N processes/paths, where N is PM-configurable (10, 20, 50, custom).

### Clarification on Layer 2's content

Layer 2 is a **variant-frequency table** — each row is one unique path/sequence through the process (not a "process model"), with a frequency count. Example:

| Rank | Path (sequence of events) | Frequency (count) | % of all sessions |
|---|---|---|---|
| 1 | view_product → add_to_cart → purchase | 42,318 | 31.2% |
| 2 | view_product → view_product → purchase | 18,904 | 13.9% |
| 3 | view_product (exit, no further action) | 15,672 | 11.6% |
| 4 | view_product → add_to_cart → remove_from_cart → view_product → purchase | 9,845 | 7.3% |
| 5 | view_product → view_reviews → add_to_cart → purchase | 8,201 | 6.1% |
| 6 | view_product → view_product → view_product → add_to_cart → purchase | 6,554 | 4.8% |
| 7 | view_product → add_to_cart → remove_from_cart (exit) | 5,982 | 4.4% |
| 8 | view_product → view_reviews → expand_product_features → add_to_cart → purchase | 4,120 | 3.0% |
| 9 | view_product → view_similar_products → view_product → purchase | 3,876 | 2.9% |
| 10 | view_product → add_to_cart → apply_coupon → purchase | 2,910 | 2.1% |

Below the PM-selected "Top N" cutoff, remaining long-tail variants are bucketed into a single "Other (X variants, Y% of sessions)" row, rather than showing every rare variant individually.

This is what PM4Py's variant-statistics functions naturally produce (distinct traces + counts, computed automatically) — the "Top N" control is a UI slider on top of that sorted list.

---

## Decision 3: Link Layer 1 and Layer 2 interactively

- **Default state:** Layer 1 (the diagram) loads showing the Rank 1 path (the single most frequent variant) highlighted/traced through the process map.
- **On interaction:** clicking any row in the Layer 2 table re-renders Layer 1 to highlight that specific path's sequence of nodes/arrows, with the rest of the diagram dimmed/greyed to make the selected path visually stand out.
- **Selection behavior:** only one path is highlighted at a time — clicking a new row replaces the previous highlight rather than stacking multiple paths.

---

## Decision 4: bupaR + processanimateR as a bonus animated-replay feature

- Positioned as a **nice bonus feature**, not core/equal priority to the PM4Py diagram + variant table.
- Docker capacity (900 GB available locally) removes any concern about running a separate R container alongside the core Python stack — no need to economize on this.
- **Implementation shape:**
  - Separate, lightweight R container (`rocker/r-ver` + bupaR + processanimateR), decoupled from the core Python/PM4Py pipeline.
  - **Trigger-based, not always-on** — the R container renders on-demand rather than running continuously.
  - **Batch-rendered**, not a fully live/scrubbable widget — simplest version: PM requests an animation (via the trigger below), R container renders it, dashboard displays the output.
  - Output likely as an HTML widget (via htmlwidgets) rather than a static GIF, to retain some interactivity (e.g., hover for token/customer info) without building live time-scrubbing from scratch.

## Decision 5: Animation is triggered contextually from Layer 2, not a separate tab

- A small **"▶ animate this path"** button/icon sits next to each row in the Layer 2 variant table (rather than a separate detached "Animated Replay" tab).
- Clicking it sends that path (+ optional date range) to the R container, which filters the event log and renders the animation.

### Scope of what's animated: contextual replay (confirmed)

Clicking ▶ animates the **whole process map**, with that specific path's tokens visually highlighted/distinguished from the rest of the traffic — not an isolated replay of only that path's cases. Chosen deliberately because isolating just one path's tokens would hide the congestion/bottleneck behavior that is the point of this feature; contextual replay shows the selected path's behavior in the context of overall traffic.

### Supporting UX decisions

- **Render time:** acceptable as-is, no optimization required — this is a local, portfolio-context project, render delay is defensible and not a concern.
- **Caching:** confirmed — rendered animations are cached (e.g., by path/variant hash), so repeat clicks on the same variant reuse the cached render rather than re-rendering every time.

### Cross-cutting flag raised by this feature: synthetic dataset must be engineered to show visible congestion

For the contextual-replay animation to be meaningful, the synthetic dataset (see Decision 1 / future generator design) must deliberately bake in congestion patterns rather than random/uniform noise. Specifically the generator should include:
- **Time-clustered spikes** — e.g., a flash-sale window where many sessions hit the same step (e.g., `add_to_cart`) within a short time span, causing visible token pile-up.
- **Slow-step simulation** — some transitions (e.g., `begin_checkout → select_payment_method`) should have deliberately longer time gaps for a subset of sessions, so tokens visibly "sit" during animation.
- **Segment-specific bottlenecks** — different segments (e.g., mobile vs. desktop) should congest at different steps, so segment-filtered animations actually look distinguishable from one another.
- **Uneven arrival rates over time** — traffic should have peak/quiet periods (time of day, day of week) rather than a flat uniform rate, so the animation timeline has visible moments of surge vs. calm.

This flag reaches back into Decision 1 (schema) and the not-yet-built dataset generator — noted here as a hard requirement to fold in when generator design happens, not yet a separate formal decision.

---

## Decision 6: Path-specific, sequential funnel with drop-off %

For any path selected from the Layer 2 variant table, the dashboard computes and visualizes step-by-step drop-off using **step-specific ("funnel-style") drop-off logic**: of all sessions that reached step X overall (not just sessions on that exact path), what % continued to step X+1. This is standard sequential/strict funnel logic — each stage counts only sessions that passed through every prior stage in order.

- **Visualization:** Apache ECharts funnel chart (shrinking bars per step).
- **Primary metric displayed:** % drop-off at each step, normalized to 100% at the path's first step.
- Raw session counts are secondary (tooltip/label), not the headline visual.
- The funnel is **tied to whichever path is selected** in Layer 2 — not a fixed, independent master funnel.

## Decision 7: Multi-path funnel comparison

- PM can select **2, 3, or 4 paths** from Layer 2 to compare funnels side-by-side.
- Selection is constrained via an options bar/selector offering only 2/3/4 as choices (hard-enforced through the input control itself, not validated after the fact).
- **Layout:** side-by-side funnels (not overlaid/grouped-bar) — chosen because compared paths can have different lengths and different step names, which an overlaid chart would handle poorly.
- **Default state (no comparison selected):** funnel section shows only the Rank 1 path's funnel.

## Decision 8: Segment profile panel (per selected path)

For any path selected in Layer 2, the dashboard retrieves the users/sessions matching that path and shows a segment breakdown, split into two types:

1. **Categorical segments** — e.g., state/region, gender, device type, traffic source, customer tenure/new-vs-returning. Displayed as small bar/pie charts per attribute (counts/percentages).
2. **Behavioral segments** — e.g., average session time, average pages/products viewed, average time-to-purchase, average cart value, repeat-visit count before converting. Displayed as a stat-card/KPI-tile grid.

(Schema implication for this decision — the required `users` dimension table — is recorded under Decision 1.)

## Decision 9: Segment comparison follows the same multi-path selection as funnels

- Segment profiles support comparison mode using the **same 2/3/4 path selection** mechanism as the funnel comparison (Decision 7) — not a separate comparison mechanism.
- **Layout:** Funnels section and Segments section are **separate, independent areas** of the dashboard (not grouped per-path together on screen).
- **Default state (no comparison selected):** segment section shows only the Rank 1 path's segment profile — mirroring the funnel section's default behavior, and consistent with Layer 1's existing default-to-Rank-1 behavior.

## Decision 10: Star/favorite paths

Product Manager has the option to **star particular paths** in the Layer 2 variant table, for quick reference/bookmarking. (Scope/behavior of starred paths — e.g., a dedicated "starred" filter view, persistence across sessions — not yet detailed; flagged as a lightweight feature to refine later if needed.)

---

## Decision 11: Process-diagram complexity at scale — mitigations

To keep Layer 1's diagram readable as dataset scale grows (avoiding the "spaghetti diagram" / "flower model" problem caused by rare long-tail paths, high event-type variety, and mixed customer segments):

- **Default frequency filtering: 60%** — Layer 1's diagram shows only paths covering the top 60% of traffic by default, hiding rare/noisy paths to keep it readable.
- **Granularity toggle confirmed** — PM can switch the diagram between full event granularity (every micro-event shown as its own node) and a simplified/abstracted view (micro-events collapsed into higher-level steps, e.g., "engaged_with_details").
- **No segment-specific diagram views** — segment-based slicing is not duplicated into the diagram; it remains exclusive to the Segments panel (Decision 8/9).

---

## Decision 12: Feature-release comparison for starred paths

For the top 5 starred paths (see Decision 10), the dashboard supports comparing each path's behavior **pre-feature vs. post-feature**. This is implemented via **two separate synthetic datasets** (a "pre-feature" generation run and a "post-feature" generation run) rather than a shared dataset with a cutoff flag:

- Both datasets share the identical schema and the same top 5 path/variant identities (same event sequences), so comparisons are apples-to-apples.
- Only the underlying behavioral probabilities/timing differ between the two generation runs (e.g., a "feature" might reduce cart abandonment or speed up a slow step for the post-feature run).
- What each synthetic "feature" specifically changes must be deliberately defined before generating the post-feature dataset (e.g., "reduces cart abandonment by 15% for mobile users"), then baked into that run's probability rules.

## Decision 13: Synthetic dataset generation parameters

- **Scale:** 1,000,000 rows total.
- **Behavior split across sessions:**
  - 50% — look-and-leave (browse, no purchase)
  - 25% — buy right away (fast conversion, minimal research)
  - 25% — research-heavy (multiple products/reviews/features viewed before purchase)
- **Congestion engineering (per Decision 5):** applied selectively to certain paths, not uniformly across the whole dataset — e.g., specific paths get flash-sale-style time-clustered spikes or deliberately slow steps, rather than every path having engineered congestion.
- **Journey definition:** a customer journey ends at purchase. Journeys that take a long time to reach purchase are not capped or corrected — the delay itself is a meaningful signal, not noise to be cleaned up.
- **Non-purchasing journeys (look-and-leave segment):** considered closed after a fixed period of inactivity (default assumption: 7–14 days with no new events) — a simple, uniform rule applied by the generator script, not a manually engineered per-case decision.
- **Search ranking logic:** search results are ordered **best-selling first**. This gives meaning to the `result_position_clicked` field in the Search Events satellite table (Decision 1) — it reflects how far down the best-seller ranking a customer had to go before clicking.

## Decision 14: Login is mandatory (no guest checkout)

Guest checkout is not an option in this project's journey model — `login` (placed in the Transactional & Session Events table, occurring at checkout per earlier discussion) is a required step in every purchasing path, not skippable.

## Decision 15: Scope boundaries — explicitly out of scope

- **Cosmograph, Embedding Atlas, Mosaic + vgplot, deck.gl** — confirmed out of scope, not being pursued further (same status as OpenReplay and FINOS Perspective).
- **API layer design and Docker container topology** — explicitly left to be handled during implementation (Antigravity/Cursor), not part of this planning/architecture discussion.
