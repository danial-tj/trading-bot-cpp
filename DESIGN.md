# Opening Bell / strategy research

Opening Bell is a local backtesting workbench for studying opening momentum, VWAP and moving-average rules. Its interface should make the saved result, editable rules and underlying candles easy to distinguish. The data is synthetic; the workspace does not imply a live feed, brokerage connection or demonstrated investment performance.

## Visual contract

Use an integrated black/charcoal workspace with readable Manrope typography, compact toolbars and a docked strategy inspector. The user explicitly preferred the black theme; the rejected elements were the generic buttons and structure. The later light research-desk composition is superseded. Preserve the existing native HTML/CSS/JavaScript and Python service.

| Role | Value |
| --- | --- |
| Canvas / chart | `#121416` |
| Header / setup dock | `#171a1d` |
| Main text | `#e1e5e9` |
| Secondary text | `#a0a8b0` |
| Structural rule / chart grid | `#2b3035` |
| Selection accent | `#88a9fa` |
| Primary control | `#dce4ed` with dark text |
| Strategy curve | `#69bda3` |
| Benchmark curve | `#919aa4`, dashed |
| Drawdown curve | `#d8828d` |

The price chart uses green/red data colors and muted indicator colors. Selection color does not encode profit or loss. Direction and selection must also remain identifiable through labels, hollow/filled candles, line styles or explicit state.

Use the bundled Manrope font for headings, controls and body copy, with tabular numerals for figures. Keep chart labels and controls readable without turning the workspace into a marketing page. Favor thin dividing rules and small control corners over rounded cards, large gutters or a hero header. Avoid decorative account avatars, fake telemetry, excessive badges, repeated status copy and ornament-only icon rails.

## Composition and behavior

- A plain wordmark and two real destinations lead the header: Workbench and Saved runs. A saved run opens its own immutable result and settings.
- The workbench integrates a wide chart, results beneath it and a right-hand setup dock. Do not separate these working surfaces into a collection of floating cards. Compact layouts move setup below the chart while preserving readable fields and touch controls.
- The chart toolbar contains the session and chart type. OHLC, indicator values and the opening-window overlay refer to actual saved candles. Session labels are readable dates; requests retain the ISO date value.
- The inspector presents rule labels paired with values from the actual editable parameters, without an onboarding stepper or invented pass/fail states. The 15/30-minute control changes the next run's settings, not the currently displayed saved result. Editing is explicit; a delayed result must not overwrite subsequent form edits.
- Desktop has one compact Run action in the workspace toolbar. A second form submission control is available only at tablet/phone widths where setup sits farther below the chart. Neutral controls replace oversized, brightly colored calls to action.
- Final equity leads the supporting performance strip. Overview, Trades and Run details expose the equity curve, execution journal and reproducibility information without competing with the main price chart.
- Saved runs use readable journal rows, not miniature cards. Display all runs returned by the service. Loading, cancellation, failure and empty data remain truthful states.
- Preserve keyboard chart inspection, labelled pan/zoom controls, radio arrow-key navigation, result-tab navigation, visible focus and inline validation. Disable chart controls while their data is unavailable. Keep the local same-origin security policy.
- Session, strategy, dataset and activity choices use app-owned dark menus rather than platform select popups. Menus anchor to their triggers, show a clear selected row and tick, constrain long lists to a scrolling region, and support arrows, Enter, Escape, Tab/Shift+Tab and outside dismissal. Session search includes a readable empty-results state. Hidden native selects remain the value source for existing form behavior.
- Validation stays in the dark interface, marks and focuses the invalid field, and does not call the browser's `reportValidity()` popup. The price chart is inert while new candles load. Opening/full-view selection and pan/zoom availability derive from the actual visible range; zooming keeps the displayed quote consistent with that range.

## Motion

Animate's purpose/frequency gate favors immediate feedback for this repeatedly used workbench. The current design has no transform or animation: hover and pressed fills, focus outlines and selected states update immediately. Charts, data changes and navigation stay still. No motion library is required.

## Resources actually applied

- The existing [portfolio toolkit handoff](../portfolio-publish/design-system/DESIGN-TOOLKIT-HANDOFF.md) identified reusable resources. Its Vancouver/pixel-art visual identity was not copied. An initial reference lookup routed to an academic portfolio; that unrelated result was discarded.
- **Taste:** audit principles informed the removal of generic scaffolding and the hierarchy review. Its skill explicitly excludes dashboards, so it was not treated as the implementation specification for this workbench.
- **UI/UX Pro Max:** interface hierarchy, typography, responsive behavior, accessibility and chart guidance were evaluated against this research task.
- **TradingView:** its [actual chart and interval dropdown](https://www.tradingview.com/chart/) were directly inspected for the menu refinement. Anchored dark menus, strong selected-row treatment and keyboard operation informed this scoped implementation; it does not reproduce TradingView's full feature set.
- **WAI-ARIA APG:** the [combobox pattern](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/) informed menu roles, active-option state and keyboard/focus behavior.
- **21st.dev:** the [Trade Journal Table](https://21st.dev/@ssychui/components/trade-journal-table) reference by `ssychui`, demo `27124`, was inspected. Its readable rows, restrained table treatment and clear numeric alignment informed the execution journal. Its fictional trades, fee formula and React implementation were not imported. Existing engine values remain authoritative.
- **Built-in ImageGen:** [docs/design-reference.png](docs/design-reference.png) informed the preceding light composition. The user's later direction restores the dark theme and changes the structure, so the image is historical reference rather than the current target. It is not an application screenshot, a functional prototype or evidence of financial results.
- **Vercel Web Interface Guidelines:** [current reference](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md) informed semantics, focus, async feedback, touch sizing and reduced-motion behavior.
- **Manrope:** copied unchanged from the existing portfolio assets and self-hosted at `/fonts/manrope.woff2`. Copyright 2019 The Manrope Project Authors; distributed with the [SIL Open Font License](service/static/fonts/Manrope-LICENSE.txt). No external font request is needed.
- **Awesome DESIGN.md:** its project-contract approach informs this record; no unrelated brand theme was imported.

Browser screenshots and verification results belong in the project's validation record. Do not treat the generated reference or this design contract as proof that a particular interaction was tested.
