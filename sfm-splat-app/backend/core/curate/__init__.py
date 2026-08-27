"""
curate — the "clean image set" engine of wizard step 2 (CLAUDE.md §6.3).

Four pure modules, no FastAPI import anywhere:

  scenes.py     cut detection  → one *sequence* id per frame
  sharpness.py  Tenengrad score + relative blur rejection
  overlap.py    ORB displacement gate against the last kept frame
  select.py     merge the verdicts, apply the manual overrides

They take plain paths and numbers and return plain data. All file I/O and all
broadcasting live in `core/steps/step_analyze.py`, which is what makes these
callable from a test without a running app.
"""
