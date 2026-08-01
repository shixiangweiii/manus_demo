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

Set model name, base URL, timeouts, paths, ports, runtime defaults, and capability switches in `settings.toml`. Do not put credentials inside `remote_agent_server_json` or other ordinary settings. CLI exposes only engine, executor, and effort because advanced options should remain reviewable in TOML.

Runtime selection, orchestration tuning, and action execution are separate:

```toml
[runtime]
engine = "auto"
executor = "auto" # auto | tool_calling | reasoning_aware_tool_calling
effort = "auto"

[engines]
max_replan_attempts = 3
max_parallel_nodes = 3
planner_temperature = 0.3
reflector_temperature = 0.1

[execution]
max_context_tokens = 16000
max_action_iterations = 10
tool_calling_temperature = 0.5
reasoning_aware_tool_calling_temperature = 0.5
max_reasoning_tokens = 10000
max_reasoning_rounds = 5
```

`effort` is a runtime resource policy. After `auto` is resolved from the
orchestration engine, the selected level can affect planning and action-loop
temperature, iteration, truncation, and reasoning budgets. It does not mean
that hidden reasoning must be displayed.

Local execution modes are ordinary TOML settings:

```toml
[tools]
shell_mode = "restricted" # disabled | restricted | trusted
python_mode = "disabled"  # disabled | trusted
```

Restricted Shell parses one command with `shlex`, executes its argv directly, rejects shell operators/expansion and sandbox escapes, and allows only documented read-oriented commands. It does not expand globs. Trusted Shell uses full bash with the current local user's permissions. Trusted Python is also not a security sandbox; subprocess cwd and timeouts do not limit filesystem or network authority.

Set `tracing.log_prompts = false` when task, plan, tool, model, or exception content must not be written to trace attributes. Only exception type, structural events, timing, identities, and status remain. When content logging is enabled, payloads still pass through recursive secret redaction.

`config.py` mirrors structured settings for retained peripheral code. It is not a second configuration source and must not receive new fields.
