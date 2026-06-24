# Opteia Skin (Kanboard plugin)

A modern, responsive, PWA-capable skin for Kanboard v1.2.x — applied **as a
plugin**, so Kanboard keeps 100% of its functionality. Disable the plugin to
revert to the stock UI.

Built by Opteia as a fork of [ThemeRevision](https://github.com/greyaz/ThemeRevision)
(MIT, by Greyaz). See `NOTICE` for attribution.

## What it does

- Modern, minimal look (Opteia brand palette) over Kanboard's existing UI.
- Auto light/dark (follows the system) with per-user override.
- Mobile-first responsive fixes (board scroll, touch targets, tables, forms).
- Installable PWA (manifest + service worker) — Phase 3.

It is a **skin**, not a replacement: every Kanboard feature (projects,
swimlanes, columns, subtasks, comments, attachments, Gantt, settings, user
management) remains intact and is themed.

## Layout

```
Plugin.php                 Hook registrations (css/js/head), template overrides
Template/                  layout.php override (+ settings/user templates)
Model/                     CustomColorModel (Opteia palette -> CSS vars), configs
Helper/                    config/mode/color-switch helpers
Controller/                settings controller
Asset/                     CSS (dev/ + main.min.css), JS, opteia-overrides.css, pwa/
Locale/                    translations
```

## Deploy (`.19`)

From the repo (`infrastructure/kanban-skin/`):

```bash
./deploy.sh
```

Packages `OpteiaSkin/`, uploads to `/opt/kanboard/plugins/OpteiaSkin/`, lints all
PHP inside the kanboard container, and restarts the container to re-scan plugins.
Reload `https://kanban-test.opteia.com`.

## Configure

- **Palette / mode:** Settings → Opteia Skin Settings (admin), or per-user
  theme in the user menu.
- **PWA service worker:** needs the nginx `location` block in
  `../nginx/sw-location.conf` added to `/opt/kanboard/nginx.conf` (Phase 3).

## License

MIT — see `LICENSE` (upstream ThemeRevision) and `NOTICE`.
