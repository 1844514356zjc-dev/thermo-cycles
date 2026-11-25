# Project separation — 2026-11-25

Two independent repositories now have separate code, tests, dashboards and CI:

- [Thermo Cycles](https://github.com/1844514356zjc-dev/thermo-cycles): steady-state Brayton/Rankine cycles and exergy analysis.
- [Modular 0D Framework](https://github.com/1844514356zjc-dev/modular-0d-framework): dynamic gas control volumes, engine kinematics and gas exchange.

Both use `src/ui/app.py` within their own repository. Thermo Cycles uses local port 8501. Modular 0D Framework uses local port 8502 for both local Streamlit and the Compose host mapping; its internal container port remains 8501. The projects can run concurrently without sharing a port.

Earlier combined commits are historical snapshots. Current validation and CI belong only to the project whose repository you are viewing; reference results from one model do not validate the other.
