# Design system: "the marked ledger" (BUILD_SPEC §11a)

## Idea

The product is a business owner's trusted analyst. The look borrows from the Indian ledger (*bahi-khata*) and from
an analyst's habit of **highlighting the one number that matters**. Screens are calm and precise: deep indigo ink on
cool paper. The single memorable gesture is a **turmeric (haldi) highlighter mark** behind the key number of every
answer, briefing and tile. Boldness is spent there and nowhere else.

Avoid: cream background with terracotta accent, black background with neon accent, identical rounded cards with the
same grey shadow, gradient washes, ALL-CAPS eyebrow labels, monospace data labels, "→" appended to buttons.

## Colour tokens (source: `design/tokens.json`, generated to CSS variables and a Tailwind preset)

Values marked † differ from BUILD_SPEC 11a.2 so that every pair meets WCAG 2.2 AA; tokens marked ‡ are new
(see `docs/DECISIONS.md` D8). Run `make tokens` after editing `design/tokens.json`.

| Token | Light | Dark | Use |
|---|---|---|---|
| `ink` | #1E1C3A | #ECEBF7 | Primary text |
| `ink-muted` | #5A5874 | #A9A7C2 | Secondary text, captions |
| `paper` | #F5F6FA | #12112A | App background (cool, never cream) |
| `surface` | #FFFFFF | #1C1B3A | Panels, answer sheets, inputs |
| `line` | #E2E4EE | #2E2D52 | Dividers, ledger rules, panel borders |
| `line-strong` ‡ | #85839F | #706E96 | Input and control borders (≥ 3:1) |
| `neel` (brand) | #3A36A8 | #8F8BFF | Primary buttons, links, active nav, focus ring |
| `neel-soft` | #ECEBFB | #26245A | Selected rows, active chips |
| `on-neel` ‡ | #FFFFFF | #12112A | Text on primary buttons (white fails on dark-mode neel) |
| `haldi` (signature) | #F2C230 | #E2B21E | Highlighter mark behind text at 55% opacity (light) / 40% † (dark), small "new" dots. Never a text colour |
| `gain` | #11795A | #4FD1A1 | Positive change |
| `loss` | #C0392B | #FF7A6B | Negative change, errors |
| `caution` | #96600A † | #F0B44C | Data-quality warnings |
| `gain-soft`, `loss-soft`, `caution-soft` ‡ | #E6F4EF, #FBECEA, #FBF3E4 | #183040, #33213A, #2E2630 | Status pill and banner backgrounds |
| `scrim` ‡ | ink at 45% | near-black at 70% | Backdrop behind sheets and dialogs |
| Chart series | #3A36A8, #11795A, #B07400 †, #8C4FBF, #2A8FB5, #C0392B | #8F8BFF, #4FD1A1, #F0B44C, #C79BEB, #6CC4E6, #FF7A6B | Fixed order; never rely on colour alone |

Every text/background pair meets **WCAG 2.2 AA** (4.5:1 body, 3:1 large text and UI). A token test checks contrast.

## Typography

- Display: **Bricolage Grotesque** 600–700 (page titles, answer lead sentence, big KPI numbers, marketing headlines),
  tracking −1% at large sizes.
- Body/UI: **Noto Sans** 400/500/600 plus the matching Noto Sans script for Devanagari, Kannada, Tamil, Telugu,
  Malayalam, Bengali and Gujarati, self-hosted through `@fontsource`.
- Numbers use `font-variant-numeric: tabular-nums`; money goes through `formatINR` (₹8,450 / ₹12.4 L / ₹1.24 Cr).
- Scale (rem, ratio 1.25): 0.75 caption · 0.875 small · 1 body · 1.25 h4 · 1.563 h3 · 1.953 h2 · 2.441 h1 · 3.815 hero.
  Line height 1.55 body (1.7 Indic), 1.15 headings. Prose ≤ 75 characters wide. Sentence case everywhere.

## Layout and shape

- 4 px grid (4, 8, 12, 16, 24, 32, 48, 64). Content is left-aligned in the app.
- Shell: desktop = collapsible left sidebar (Today, Ask, Dashboard, What-if, Goals, Briefings, Tasks; Owners also get
  Connections, Team, Activity, Settings); tablet = icon rail; mobile = bottom tab bar (Today, Ask, Dashboard, More)
  plus a floating Ask button.
- Radius by hierarchy: panels 16 px, inputs/buttons 10 px, chips/badges pill, tables and the answer sheet 4 px.
  Depth comes from `line` borders and background contrast. Shadows only on overlays (menus, dialogs, drawer).
- Ledger rules: thin `line` horizontal rules, 44 px rows, no zebra stripes, no vertical borders.

## Signature components (built in Storybook first)

Highlight (haldi sweep; once per view) · AnswerSheet · SourceMark (superscript → popover with source, query, fetch
time) · QualityBanner · ProgressSteps · KpiTile · ScenarioCard · ConnectionCard · EmptyState · plus shadcn/ui Toast,
Dialog, Drawer, Tabs, Table, Select, DatePicker, Slider restyled with tokens.

## Motion

One orchestrated moment: the lead sentence fades in (150 ms) and the haldi mark sweeps left-to-right behind the key
number (400 ms, ease-out). Everything else only responds to actions (drawers 200 ms, dialogs 150 ms, progress ticks).
No scroll-entrance animation or card hover lift. `prefers-reduced-motion` removes the sweep and slides.

## Writing in the UI

Plain, active, sentence case. Buttons say what happens ("Connect Google Sheets", "Ask", "Run scenario"). Errors say
what happened and how to fix it, never apologise. Use the business's own words (orders, outlets, pumps), not system words.

## Quality floor

Responsive at 375 / 768 / 1280 / 1536 px · touch targets ≥ 44 px · 2 px `neel` focus ring with 2 px offset ·
full keyboard navigation · light and dark (follows system, user can override) · shell JS < 200 KB gzipped ·
Playwright snapshots for each main screen (light/dark × desktop/mobile) reviewed at each milestone.

## Where things live (M1b)

- `design/tokens.json` → `node design/build.mjs` → `design/generated/tokens.css` (CSS variables; light by default,
  dark via `prefers-color-scheme` unless `data-theme="light"`, or forced with `data-theme="dark"`) and
  `design/generated/tailwind-theme.css` (the Tailwind v4 preset: an `@theme` block that removes Tailwind's default
  colours/fonts/radii so only tokens can be used). The Astro site (M10b) imports the same two files.
- Breakpoints (Tailwind variants): `tablet:` ≥ 768 px (icon rail), `desktop:` ≥ 1280 px (sidebar), `wide:` ≥ 1536 px.
- Components: `frontend/src/components/` (signature components) and `components/ui/` (restyled Radix primitives:
  Button, Popover, Slider, Sheet). Stories in `frontend/src/stories/`, each with Light, Dark and Phone variants.
- Checks: contrast test (`src/design/tokens.test.ts`), Playwright snapshots + axe + layout (`frontend/e2e/`,
  run with `make e2e`), shell JS size (`make size`).
