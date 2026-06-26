# Modacthon QA Input Prototype

Practice workspace for the Smilegate Modacthon QA track.

The current `v1` prototype focuses on the input axis:

```text
human/scripted input -> recording or scenario JSON -> replay -> repeatable QA action flow
```

## Structure

- `assets/`: shared CrossFire/TDM/map/scenario reference JSON files.
- `v1/`: input recording and replay prototype.

## Notes

- `.venv/` is intentionally not tracked.
- Runtime recordings under `v1/recordings/*.json` are ignored by default.
- CrossFire fullscreen may block keyboard capture from user-mode Python; `v1` includes hook, polling, and raw-input recording attempts plus replay tooling.
