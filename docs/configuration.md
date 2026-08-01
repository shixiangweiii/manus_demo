# Configuration

Configuration precedence is fixed:

1. dataclass defaults in `core/settings.py`
2. normal values from `settings.toml`
3. whitelisted secrets from `.env` or the process environment
4. CLI overrides for one run

Unknown TOML sections or fields fail at startup. Extra non-secret keys in `.env`
also fail with a migration hint. Normal process environment variables are intentionally ignored.

`.env` supports only:

```env
LLM_API_KEY=
DASHSCOPE_API_KEY=
AGENTBAY_API_KEY=
```

Set model name, base URL, timeouts, paths, ports, engine defaults, and capability switches in `settings.toml`. Do not put credentials inside `remote_agent_server_json` or other ordinary settings. CLI exposes only engine, executor, and effort because advanced options should remain reviewable in TOML.

Set `tracing.log_prompts = false` when task, plan, tool, or model content must not be written to trace attributes. Structural events, timing, identities, and status remain available.

`config.py` mirrors structured settings for retained peripheral code. It is not a second configuration source and must not receive new fields.
