# WeKnora file mention selection evidence

Code commit: `769dec101e391bbc5dec46dd7297d6317a49e532`. Baseline: `234aa948db29c10f5ed4b7939285c7b3711c5d4c`.

Screenshots were captured from the real Input-field.vue and MentionSelector.vue mounted with Vue, Pinia and TDesign. HTTP responses use synthetic knowledge bases and files; no live backend or user data is involved.

- before.png: original single-file selection menu.
- pending.png: two files checked, before confirmation.
- confirmed.png: two references added and the typed @ trigger removed once.

Windows, Node 24.20.0, Chrome 155.0.8059.40. Two baseline reproduction cases and sixteen browser interaction cases passed. IME coverage uses synthetic composition events; other browsers and a live backend were not tested.
