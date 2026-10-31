# Contributing

Report the application version, operating system and architecture, recording profile, sampling rate, selected samples/channels, settings and observed result. Include a small synthetic reproducer or a lawful public-data link when possible. Keep private paths and sensitive data out of public issues.

Run `python -m pytest tests -q` before proposing a source change. On a headless host use `QT_QPA_PLATFORM=offscreen`; this is a software-test route, not evidence of successful desktop interaction. Numerical changes need a separately implemented reference, explicit tolerances, quantity/unit checks, and an explanation of how old results and checkpoint identity are affected.

Do not silently reinterpret ambiguous units, gauge length, coordinate origins, timestamps or source labels. New native readers need a documented profile, small fixture tests, source-data provenance, and bounded real-data validation. A new GPU backend needs CPU agreement and recorded fallback behavior before it is described as validated.

Contributions to the application and original documentation use the repository MIT licence. Preserve dependency notices and original data attribution. No requirement to assign copyright is imposed here.
