# Phase 3 acceptance: VIRAT clips

Ground truth for the Phase 3 acceptance test (`tests/acceptance/test_phase3.py`): one YAML per
clip with the entities, the actions (in the MVP action vocabulary, seconds from the start of the
clip) and the queries whose interval must be in the top 3 results.

The videos are not committed. Each YAML names its clip by path; they live in
`test-data/virat/motion/` (VIRAT Video Dataset Release 2.0, ground camera clips, viratdata.org).
Download a clip from data.kitware.com (collection "VIRAT"):

    https://data.kitware.com/api/v1/resource/search?q=VIRAT_S_040103_05_000729_000804&types=["item"]
    https://data.kitware.com/api/v1/item/<item id>/download

How the YAML files were made:

1. `python -m tests.acceptance.virat_events VIDEO EVENTS_TXT OBJECTS_TXT -o NAME.yaml` drafts
   the events from VIRAT's `*.viratdata.events.txt` and `*.viratdata.objects.txt` (frames are
   converted with the video's own frame rate).
2. Every event was checked by eye on frames every 4-5 s; times were corrected, events merged,
   and clip-long "someone walks" / "traffic moves" spans removed (they would be found trivially).
   Vehicle movements from the tracks were named by what they show (arrive, depart).
3. Queries were written after watching the clip.

Two fields record what the VLM can't do on these clips (the decision is in the project's ADRs):

- `actions_needed: 0.7` in every file: VIRAT's wide views make some actions small and far away,
  so 70 % of the actions must be found instead of the default 80 %. Our own recorded clips keep
  the default.
- `known_miss: <reason>` on one query of `VIRAT_S_040104_07_001268_001348`: the VLM never
  describes the short, distant unloading. The query still runs and is reported, and is flagged if
  it starts passing; it doesn't fail the test.

Clips chosen: 30-90 s, at least two clear events, people not tiny in the picture
(scenes 0401, 0500, 0503). Rainy or very distant clips (050203_01, 050300_00) were left out.
