---
name: log-triage
description: Count log lines per level with the bundled script and write a triage report
version: "1.0.0"
---

# log-triage

When asked to triage a log file:

1. Run `scripts/count_levels.py` with `run_script`, passing the log file's name as its
   one argument. It prints one `LEVEL: count` line per level, sorted by level name.
   Do not count the lines yourself; the script is the source of truth.
2. Read `references/report-format.md` with `read_skill_file` and follow it exactly.
3. Write `triage.md` with `write_file`.

Never report a level the script did not print.
