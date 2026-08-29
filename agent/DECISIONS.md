# Decisions

- **03 stays the producer.** Multi-source is **04**, because the user asked not to change 03.
- **Bronze reads `sources/`**, not the generator dump.
- **`generator_src/` in git** is extracted from 01 so Cursor can run locally. Editing Python means updating 01 cells too, or they drift.
- **Branch name** `cursor/...-cf00` was required by the Cloud Agent. Keep using it until merge to `develop`.
- **No secrets in git.** Tokens in chat are compromised.
- **`agent/` is the handoff.** Update PLAN/DONE/LOG instead of scattering notes in chat only.
