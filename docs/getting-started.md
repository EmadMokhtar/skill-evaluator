# Getting started

A skill is a directory containing `SKILL.md`. Its eval cases live beside it:

```
examples/
  greeting/
    SKILL.md
    greeting.eval.yaml
```

The `SKILL.md` frontmatter declares the skill's name, description, and optional version:

```yaml
---
name: greeting
description: Greet a user warmly and by name
version: 1.0.0
---
```

`version:` is optional. When present, `--baseline previous` uses it to find the
previous version of the skill in git history — see
[Comparative evals](comparative-evals.md).

```yaml
# greeting.eval.yaml
cases:
  - name: greets the named person in one sentence
    task: greet Ada
    tags: [smoke]
    budget:
      max_tokens: 500
    assertions:
      - kind: contains
        value: Ada
      - kind: not_contains
        value: Traceback
      # SKILL.md asks for "one short sentence"; this regex only checks "one line,
      # under 120 chars, ending in . ! or ?" -- it doesn't (and can't, with a
      # regex) verify single-sentence-ness. It's deliberately looser than that
      # prose because real model output legitimately varies (e.g. two short
      # clauses joined by a comma), so don't tighten it without re-recording
      # against a real provider.
      - kind: regex
        value: "^[^\\n]{1,120}[.!?]\"?\\s*$"
```

Point the CLI at a single skill directory or at a parent directory of many — discovery is
recursive:

```bash
uv run skill-eval list ./examples
```

```
greeting	1 case(s)	examples/greeting
order-support	5 case(s)	examples/order-support
```

`list` discovers skills and validates every eval file without calling a runner — free, and no
API key required. The shipped examples assert real model behavior, so actually running them
(`skill-eval run`) needs the `pydantic-ai` runner — see [running against a real agent](runners.md). The
zero-cost `fake` runner (the default) is what the test suite itself runs on.

Two of the example cases go further than a runner: one is graded by an
[LLM judge](eval-files.md#judging-output-quality) and needs `judge = "pydantic-ai"` in
`skill-eval.toml` as well, because the judge is configured independently of the runner; two
more use [`mode: offered`](eval-files.md#did-the-agent-reach-for-the-skill) to measure
whether the agent reaches for the skill at all, which only a real runner can answer. Under
the defaults both report **errored** rather than passing — nothing was verified, so nothing
is reported as verified.

Next: the full [eval file reference](eval-files.md), or
[running against a real agent](runners.md).
