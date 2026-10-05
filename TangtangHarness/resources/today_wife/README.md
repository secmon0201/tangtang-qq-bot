# Today Wife Narrative Packs

Each `*.json` file is an independent, UTF-8 content pack. The service loads
files in filename order and keeps gameplay logic separate from copywriting.
Use a descriptive filename such as `festival_lanterns.json` or
`night_market_voices.json`.

Every pack must use this shape:

```json
{
  "schema_version": 1,
  "applies_to": {
    "theme_ids": ["night_market"],
    "script_ids": ["night_market_1"],
    "mechanisms": ["摊位暗号"]
  },
  "styles": { "现实日常": { "openings": [], "aftermaths": [], "endings": [] } },
  "events": { "direct_positive": [] },
  "layers": { "market_crowd": [] }
}
```

`applies_to` is optional. When present, every specified condition must match
the live event. It prevents a seasonal or location-specific sentence from
appearing in an unrelated theme. Valid event keys are `direct_positive`,
`direct_negative`, `response`, `assist_positive`, `assist_negative`, `chain`,
and `same_scene`.

Allowed placeholders are `{actor}`, `{target}`, `{left}`, `{right}`, `{prop}`
and `{mechanism}`. Do not repeat a template found in another pack: startup and
the audit command reject duplicates across the entire active content set.

Run this after every content change:

```powershell
.venv\Scripts\python.exe scripts\audit_today_wife_narrative.py --samples 2048
```

The audit validates JSON, scopes, placeholders, duplicates, actual per-theme
combination capacity, and sampled uniqueness. Use
`scripts\render_today_wife_game_review.py` to regenerate the card review pack.
