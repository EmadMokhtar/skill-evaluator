---
applyTo: "tests/**"
---

# Reviewing tests

- **Test-driven:** the failing test comes first. A PR adding behavior with no test that
  would have failed before it is incomplete.
- **The default tier is zero-cost, offline and deterministic.** `pytest` runs with
  `--block-network` and deselects `integration`. Flag any default-tier test that could reach
  the network, need a key, or depend on wall-clock time or ordering.
- **Marker tiers:** unmarked = offline pipeline (`FakeRunner`); `cassette` = replays
  recorded provider traffic, free, selected by default; `integration` = real API spend,
  opt-in only.
- **`conftest.py` chdirs every test into a fresh `tmp_path`** so config upward-discovery
  cannot pick up an ambient `skill-eval.toml`. A test that reads repository files must
  anchor on `Path(__file__).resolve().parents[1]`, never `Path.cwd()`. Flag any test relying
  on the working directory.
- **Cassettes must be secret-free.** Both request and response headers are scrubbed, on two
  different vcrpy hooks. Flag anything that could write a credential or an account
  identifier to disk.
- A test asserting an error path should assert the message a user actually sees, not just
  the exception type.
