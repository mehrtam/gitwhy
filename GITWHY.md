# GITWHY · provenance digest · keystroke-decoder (demo data)

Purpose: WHY each file exists, recovered from AI coding sessions.
Agents: read this before exploring; it replaces re-deriving intent from grep.

## eval/chrono.py  · layer: evaluation
- 2026-07-26 [claude] add a Weibull-based OOD gate so the decoder abstains instead of guessing on out-of-distribution hand shapes
- 2026-07-27 [codex] eval report generator — one HTML page per run with confusion matrix, per-subject LOSO table, drift curves
- commit c1a8f02: Weibull OOD gate: abstain on out-of-distribution hand shapes

## eval/report.py
- 2026-07-27 [codex] eval report generator — one HTML page per run with confusion matrix, per-subject LOSO table, drift curves
- commit d9e4b77: Per-run HTML eval reports with inline-SVG confusion matrix

## features/posture.py
- 2026-07-25 [codex] rolling EMA baseline for wrist posture — sessions drift within minutes, the fixed baseline from calibration …
- commit b7d0e55: Replace static posture baseline with rolling EMA (90s half-life)

## models/dann.py  · layer: model
- 2026-07-24 [claude] the per-user calibration is overfitting — LOSO accuracy drops 14 points on unseen subjects. can we try …
- commit a3f9c21: Add DANN subject-adversarial branch with GRL warmup

## models/ood_gate.py
- 2026-07-26 [claude] add a Weibull-based OOD gate so the decoder abstains instead of guessing on out-of-distribution hand shapes
- commit c1a8f02: Weibull OOD gate: abstain on out-of-distribution hand shapes

## train_v4.py  · layer: training
- 2026-07-24 [claude] the per-user calibration is overfitting — LOSO accuracy drops 14 points on unseen subjects. can we try …
- 2026-07-25 [codex] rolling EMA baseline for wrist posture — sessions drift within minutes, the fixed baseline from calibration …
- commit a3f9c21: Add DANN subject-adversarial branch with GRL warmup
