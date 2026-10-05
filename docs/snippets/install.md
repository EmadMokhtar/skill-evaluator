```bash
uv tool install "skill-lens[pydantic-ai]"
```

`pip install "skill-lens[pydantic-ai]"` works the same way. The `pydantic-ai` extra installs
the real-agent runner and judge; without it, `skill-lens` still runs the offline default
runner. The `langchain` extra installs the LangChain runner and judge the same way, and the
two can be installed together: `"skill-lens[pydantic-ai,langchain]"`. From a checkout of this
repository, `uv sync --extra pydantic-ai` (or `--extra langchain`) installs the same thing,
and every command then runs as `uv run skill-lens ...`.
