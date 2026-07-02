---
name: camofox-browser
description: "Drive the Camofox anti-detection browser (local Firefox/Camoufox at localhost:9377) to browse logged-in sites, fill forms, and extract data. Use browser_snapshot/browser_vision to inspect pages — NOT browser_console (JS eval is unsupported). A tool error or 'tab not found' is NOT an outage: recover by recreating the tab, never by restarting the service."
version: 1.0.0
tags: [browser, camofox, camoufox, browsing, scraping, automation, web]
---

# Camofox Browser — Anti-Detection Local Browsing

Camofox is the local, always-available browser backend (a Firefox/Camoufox server with
fingerprint spoofing). It is how you browse **logged-in**, **JS-rendered**, or
**bot-protected** pages — anything a plain HTTP fetch can't see. Reach it at
`http://localhost:9377` (the `CAMOFOX_URL` env var points here).

The native browser tools (`browser_navigate`, `browser_snapshot`, `browser_click`, …)
route through Camofox automatically when `CAMOFOX_URL` is set. Use those.

> **Read this first.** Most "camofox is down" reports are **false alarms** caused by
> misusing the tools or mishandling a reaped tab. The service is almost always healthy.
> Follow the rules below before declaring an outage.

---

## The workflow

Pages are rendered as a **text accessibility tree** (aria snapshot). Interactive elements
get **ref IDs** like `@e1`, `@e2`. You act by ref.

```
browser_navigate(url)     → returns the page + an auto-snapshot with refs
browser_snapshot()        → re-read the page (after clicks/loads/waits)
browser_click(ref)        → click element by ref (e.g. "@e3", leading @ optional)
browser_type(ref, text)   → type into a field by ref
browser_scroll(direction) → "up" / "down"
browser_back()            → go back
browser_press(key)        → e.g. "Enter", "Tab"
browser_vision(question)  → screenshot + AI analysis (see below)
browser_close()           → close the session when done
```

Typical loop: **navigate → snapshot → click/type by ref → snapshot → repeat.** Always
re-`snapshot` after an action that changes the page — refs are only valid for the
snapshot they came from.

---

## ⚠️ The four rules (these are the whole point of this skill)

### 1. Inspect with `snapshot` or `vision` — NEVER `browser_console` / JS evaluation
Camofox **does not support JavaScript evaluation.** `browser_console` returns:
> *"JavaScript evaluation is not supported by this Camofox server. Use browser_snapshot or browser_vision to inspect page state."*

This is **by design, not a bug.** To see page state use:
- **`browser_snapshot()`** — the aria tree (default; cheap; gives refs to act on).
- **`browser_vision(question)`** — screenshot + vision model. Use for visual layout,
  canvases, images, CAPTCHAs, or when the aria tree is ambiguous (e.g. SPAs that render
  late). `browser_get_images()` lists page images.

Do **not** conclude "camofox is broken" from a `browser_console` error. It just means
"use the other tool."

### 2. "Tab not found" = the tab was reaped, not an outage
Inactive tabs/sessions are closed after a timeout (see *Tuning* below). If you paused
mid-task (drafting, waiting on a human) and the next op returns **tab-not-found / no
session**, the browser is fine — *your tab* is gone. **Recover, don't restart:**
1. `browser_navigate(url)` again — this opens a fresh tab.
2. Re-do any auth/login the page requires (sessions/cookies may persist depending on config).
3. Continue.

**Never** run `docker restart`, `docker exec … grep server.js`, or hand-roll a
`browser_camofox.py` / custom client to "fix" it. Those don't help and one of them got
hardline-blocked by the safety filter last time.

### 3. The first call after idle can be slow — that's normal, retry
The browser process shuts down when idle and **relaunches on demand** on the next
request (a cold launch takes a few seconds). A slow first response ≠ failure. Allow time
or retry once before treating it as an error.

### 4. Verify before escalating
Before reporting camofox as down, check the service directly:
```bash
curl -sf http://localhost:9377/health && echo "camofox UP" || echo "camofox DOWN"
```
`200` = the service is up; your problem is a tool/tab issue, not an outage. Only
escalate to a human if `/health` actually fails **and** a retry didn't recover.

---

## When to use Camofox vs. alternatives

| Need | Use |
|------|-----|
| Logged-in / authenticated browsing | **Camofox** |
| JS-rendered or bot-protected pages | **Camofox** |
| Visual inspection / "what's on screen" | **Camofox `browser_vision`** |
| Simple static HTML / public docs | plain `fetch` / `browse` tool (cheaper) |

---

## Advanced: the plugin's raw `camofox_*` tools
The `@askjo/camofox-browser` plugin also exposes lower-level tools you may see:
`camofox_create_tab`, `camofox_list_tabs`, `camofox_navigate`, `camofox_snapshot`,
`camofox_click`, `camofox_type`, `camofox_scroll`, `camofox_screenshot`,
`camofox_import_cookies`, `camofox_evaluate`. These map onto the same backend. Prefer
the native `browser_*` tools for normal work; reach for `camofox_import_cookies` /
`camofox_list_tabs` only when you need cookie injection or explicit tab management.

---

## Tuning (operator note — why tabs don't vanish mid-work anymore)
Default idle timeouts are aggressive (5 min). The shipped `docker-compose.abi-api.yml`
camofox service sets:
- `TAB_INACTIVITY_MS=1800000` (30 min) — tabs survive review/drafting gaps.
- `BROWSER_IDLE_TIMEOUT_MS=3600000` (60 min) — the browser process stays warm longer.

If a box still reaps tabs too fast, check these are set on the container
(`docker inspect abi-camofox`). The browser **always relaunches on demand** regardless
(`ensureBrowser()`), so even a fully idle browser recovers on the next request — rule 2
and 3 still apply.
