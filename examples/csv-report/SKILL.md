---
name: csv-report
description: Summarise a CSV of regional sales into a Markdown report and a JSON totals file
version: "1.0.0"
---

# csv-report

When asked to summarise a sales export:

1. Read the CSV with `read_file`.
2. Write `report.md` with `write_file`. Give it a `# Sales report` heading, one
   line per region in the form `- <region>: <units> units`, and a final
   `**Total: <units> units**` line.
3. Write `totals.json` with `write_file`, an object with a `total` integer and a
   `regions` object mapping each region name to its integer unit count.

Never invent a region that is not in the input.
