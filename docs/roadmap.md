# Roadmap

| Milestone | Contents | Status |
| --- | --- | --- |
| M0 | Scaffolding, config, CLI skeleton, release plumbing | shipped |
| M1 | Loaders, protocols, `FakeRunner`, assertion evaluator, orchestrator, console + JSON reporters, gating | shipped |
| M2 | PydanticAI runner, trajectory + budget evaluators, cost/latency capture, cassette test tier | shipped |
| M3 | LLM-as-judge evaluator (per-check verdicts), triggering evals with negative controls | planned |
| M4 | Comparative evals: `--baseline`/`--repeat`, delta reporting, `--min-delta` gating | planned |
| M5 | CI/CD polish: JUnit XML + Markdown/HTML reporters, GitHub Action, automated release | planned |
| M6 | Real-execution tools: sandboxed built-in toolset, `file-produced`/`json-schema` assertions | planned |
| M7 | DX: `skill-eval init` scaffolder, more examples | planned |
| M8 | LangChain adapter (optional) | planned |
