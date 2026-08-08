# Student Avatar Design System

Single source of truth for student / concurrent / admin web UIs.

## Direction

Classroom digital-human console: dark, calm, tool-like. Not marketing landing.
One job per page: talk (`/`), multi-lane stress (`/concurrent`), manage assets (`/admin`).

## Color tokens

```css
--bg: #0f1419 ~ #101820;
--panel: #1a222d;
--text: #e8eef5;
--muted: #8b9bb0;
--accent: #3d8bfd;   /* admin may use #2f7de0 */
--ok: #3ecf8e;
--warn: #f0b429;
--err: #f07178;
--line: #2a3545;
```

Background: deep navy with soft radial/linear gradients (not flat fill, not purple glow).

## Typography

- UI stack: `"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif`
- Mono for logs: `ui-monospace, SFMono-Regular, Menlo, monospace`
- Hierarchy: page title 22px/600 → status 15px → hint/meta 12–13px muted

## Layout

- Student: centered stage `min(720px, 96vw)` 16:9 + HUD panel below
- Concurrent: responsive card grid `minmax(280px, 1fr)`
- Admin: tabbed sections, tables, no decorative cards in hero

## Components

- Primary button: accent fill, 10px radius
- Secondary / ghost: muted fill or transparent + line
- Danger: deep red for interrupt / close-all
- Form controls: dark `#0f1520` field, `#2a3545` border, 8px radius
- Status: green idle/ok, warn busy, err failure — text, not badge piles

## Motion

Keep light: video opacity fade-in on subscribe; recording button state; avoid glow/pulse noise.

## Rules

- Reuse tokens above; do not introduce purple-on-white or cream/serif themes
- Prefer existing HTML pages over new frameworks
- Admin/tools may use compact tables; student stage stays one composition (video + controls)
