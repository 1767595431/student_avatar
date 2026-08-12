# Student Avatar UI Design

Visual source of truth: `apps/web/` only.

- Styles: `apps/web/css/common.css`, `apps/web/css/app.css`
- Pages: `index.html`（学生端）、`admin.html`（管理端）、`concurrent.html`（并发联调）、`monitor.html`（总控监听）
- Nav helpers: `apps/web/js/nav.js`

## Direction

Dark glass console: deep canvas `#030014`, purple–cyan accent gradient, Outfit typeface.

## Tokens (common.css)

| Token | Value |
|---|---|
| `--bg-deep` | `#030014` |
| `--bg-card` | `rgba(255,255,255,0.04)` |
| `--border` | `rgba(255,255,255,0.08)` |
| `--text` | `#f1f5f9` |
| `--text-muted` | `#94a3b8` |
| `--accent` | `#a78bfa` |
| `--accent-2` | `#22d3ee` |
| `--success` | `#34d399` |
| `--danger` | `#f87171` |
| `--radius` | `16px` |
| `--font` | `"Outfit", "PingFang SC", "Microsoft YaHei", sans-serif` |

Primary CTA: `.btn.btn-primary`. Panels: `.panel`. Forms: `.form-select` / `.form-input`.

## Rules

- Do not add a second theme or parallel frontend tree.
- Reuse existing CSS/components; keep API wiring when restyling.
- Confirm/prompt via `dhConfirm` / `dhPrompt` in `nav.js`.
