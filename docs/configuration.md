# Configuration

Configuration precedence is fixed:

1. defaults in `core/settings.py`
2. normal values from `settings.toml`
3. whitelisted secrets from `.env` or the process environment
4. CLI overrides for one run

Unknown TOML sections or fields fail at startup. `.env` supports only secret keys such as `LLM_API_KEY`, `DASHSCOPE_API_KEY`, and `AGENTBAY_API_KEY`; normal model, path, port, engine, effort, capability, and budget settings belong in TOML.

Runtime selection has two dimensions:

```toml
[runtime]
engine = "agent_loop" # sequential | dag | agent_loop
effort = "auto"       # auto | low | medium | high
```

There is no automatic engine selector and no executor setting. CLI `--engine` and `--effort` override only one run. Optional capabilities remain explicit feature switches and do not change the engine identity.

In the current implementation, `effort` adjusts model temperature, loop or Action turn limits, tool-result truncation, and the Plan-and-Execute ActionLoop reasoning cap. It does not currently change Planner depth and does not require hidden reasoning to be displayed.

Local execution modes are ordinary TOML settings:

```toml
[tools]
shell_mode = "trusted" # disabled | restricted | trusted
python_mode = "trusted" # disabled | trusted
```

The dataclass fallback defaults remain restricted/disabled, but this learning checkout explicitly enables both trusted modes in `settings.toml`. Restricted Shell executes one allowlisted argv command in the sandbox and rejects shell operators, expansion, and sandbox escapes. Trusted Shell and trusted Python run with the current local user's permissions; they are not security sandboxes.

Set `tracing.log_prompts = false` when task, plan, tool, model, or exception content must not be written to trace attributes. When content logging is enabled, payloads still pass through recursive secret redaction.
