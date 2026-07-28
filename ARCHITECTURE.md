# Architecture — WatsonX + HCP Boundary AI Agent

This document describes the full system architecture: components, data flows, session lifecycle, authentication paths, and design decisions.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Developer Machine                        │
│                                                                 │
│   ┌──────────┐     ┌──────────────────────────────────────┐    │
│   │ main.py  │────▶│              agent.py                │    │
│   │  (REPL)  │◀────│         ReAct loop (LLM)             │    │
│   └──────────┘     └────┬──────────┬──────────┬───────────┘    │
│                         │          │          │                 │
│               ┌─────────▼──┐  ┌────▼──────┐  │                 │
│               │watsonx_llm │  │ boundary_ │  │                 │
│               │    .py     │  │ session   │  │                 │
│               └─────────┬──┘  │   .py     │  │                 │
│                         │     └────┬──────┘  │                 │
│                         │          │ proxy   │                 │
│                         │     127.0.0.1:PORT  │                 │
│                         │          │          │                 │
│                         │     ┌────▼──────────▼─────────────┐  │
│                         │     │         ssh_exec.py          │  │
│                         │     │  (paramiko Transport + PTY)  │  │
│                         │     └─────────────────────────────┘  │
└─────────────────────────┼───────────────────────────────────────┘
                          │                    │
                          │ HTTPS              │ SSH-over-TCP
                          ▼                    ▼
               ┌──────────────────┐   ┌────────────────────────┐
               │  WatsonX.ai API  │   │    HCP Boundary        │
               │  (IBM Cloud)     │   │    Cluster             │
               │                  │   │  (HashiCorp Cloud)     │
               │  IBM Granite /   │   │                        │
               │  Llama model     │   │  ┌──────────────────┐  │
               └──────────────────┘   │  │  Boundary Worker │  │
                                      │  │ (credential inj) │  │
                                      │  └────────┬─────────┘  │
                                      └───────────┼────────────┘
                                                  │ SSH
                                                  ▼
                                      ┌───────────────────────┐
                                      │   Ubuntu Target (EC2) │
                                      │   172.31.x.x          │
                                      └───────────────────────┘
```

---

## Components

### `main.py` — Terminal REPL

The entry point. Accepts an optional `--auth {password,oidc}` CLI flag (parsed by `argparse`). On startup it:

1. Resolves the auth method — CLI flag → interactive TTY prompt → `BOUNDARY_AUTH_METHOD` env var.
2. Prompts for the Boundary password with `getpass` (hidden input) when using password auth and `BOUNDARY_PASSWORD` is not already in the environment.
3. Prints a startup banner (Rich `Rule`) showing the chosen auth method and inactivity timeout.
4. Starts an **inactivity timer** (`threading.Timer`) that automatically calls `ssh_exec.disconnect()` and `boundary_session.disconnect()` if no user input is received within `INACTIVITY_TIMEOUT_SECONDS` (default: 3600 seconds / 1 hour).

The main loop displays a `[connected ✓]` / `[disconnected]` status indicator in the prompt, resets the inactivity timer on every user turn, passes each user message to `agent.run()`, and prints the response. `readline` is imported (side-effect only) to activate arrow-key navigation and command history in `input()`. Registers an `atexit` handler that cancels the timer and calls `ssh_exec.disconnect()` + `boundary_session.disconnect()` on any exit path.

---

### `agent.py` — ReAct Loop

The reasoning core. Implements the **ReAct (Reason + Act)** pattern:

```
User message
     │
     ▼
 Build messages list  ← system prompt + conversation history + new message
     │
     ▼
 LLM call (watsonx_llm.chat)
     │
     ├── {"action": "tool_name", "args": {...}}  ──▶  execute_tool()
     │         │                                           │
     │         │◀──────── observation string ─────────────┘
     │         │
     │    (loop, max 10 steps)
     │
     └── {"action": "final_answer", "answer": "..."}  ──▶  return to REPL
```

**Tools exposed to the model:**

| Tool | Description | Idempotent |
|---|---|---|
| `connect_to_host` | Open Boundary session + SSH shell | Yes |
| `run_command` | Execute shell command, return stdout + exit code | — |
| `check_connection_status` | Report Boundary proxy + SSH state | Yes |
| `disconnect_from_host` | Close SSH + Boundary session | Yes |

The system prompt enforces strict JSON-only responses and rules around connection lifecycle. The LLM output parser strips markdown fences and extracts the first valid `{...}` block.

---

### `watsonx_llm.py` — LLM Wrapper

Thin wrapper around the WatsonX.ai `ModelInference` SDK. Uses the `/ml/v1/text/chat` endpoint (not the deprecated `/ml/v1/text/generation`). The model client is lazily initialised and cached for the session lifetime.

- Prepends a system message if the caller hasn't supplied one
- Returns the stripped `choices[0].message.content` string
- Model is configurable via `WATSONX_MODEL_ID` env var (default: `meta-llama/llama-3-3-70b-instruct`)

---

### `boundary_session.py` — Boundary Session Lifecycle

Manages authentication and the long-lived proxy process.

**Authentication flow:**

```
BOUNDARY_AUTH_METHOD=password          BOUNDARY_AUTH_METHOD=oidc
          │                                       │
          ▼                                       ▼
boundary authenticate password         boundary authenticate oidc
-password=env://_BOUNDARY_PASSWORD    -auth-method-id=amoidc_...
          │                                       │
          └───────────────┬───────────────────────┘
                          ▼
               BOUNDARY_TOKEN set in os.environ
```

**Connection flow:**

```
boundary connect -target-id=tssh_... -format=json
          │
          ▼  (first line of stdout)
  {
    "address": "127.0.0.1",
    "port": 49220,
    "session_id": "s_...",
    "credentials": [{"credential": {"username": "...", "password": "..."}}]
  }
          │
          ├── proxy process stays alive (Popen)
          └── injected credentials extracted and stored in _session_info
```

The proxy process (`_session_proc`) remains running for the lifetime of the chat session. `disconnect()` terminates it cleanly with a 5-second timeout before killing.

---

### `ssh_exec.py` — Persistent SSH Shell

Manages the paramiko Transport and interactive shell channel.

**Connection flow:**

```
socket.create_connection(127.0.0.1, proxy_port)
          │
          ▼
paramiko.Transport(sock)
          │
          ▼
transport.auth_none(username)          ← Boundary accepts auth_none
          │                              (credential injection at Worker layer)
          │  BadAuthenticationType?
          ├── auth_password(username, injected_password)
          │
          ▼
transport.open_session()
          │
          ▼
channel.get_pty(term="dumb", width=220, height=50)
          │
          ▼
channel.invoke_shell()
          │
          ▼
PROMPT_COMMAND=''                      ← silence cloud metadata noise
          │
          ▼
_read_until_prompt()                   ← drain banner / MOTD
          │
          ▼
  Shell ready — _transport + _shell stored as module state
```

**Command execution flow:**

```
run_command("df -h /")
          │
          ▼
sentinel = "__X<uuid>__"
shell.sendall("df -h / ; echo sentinel$?\n")
          │
          ▼
_read_until_prompt()   ← reads until "$ " appears
          │
          ▼
Parse output:
  line 0:  "df -h / ; echo sentinel$?"   ← terminal echo, skip
  line 1+: actual command output          ← collect
  sentinel line: "__X...0"               ← extract exit_code=0, stop
          │
          ▼
return {"stdout": "...", "stderr": "", "exit_code": 0}
```

Shell state (working directory, environment variables) persists across all `run_command()` calls in the same session.

---

## Session Lifecycle

```
User starts python main.py [--auth password|oidc]
          │
          ▼
_resolve_auth_method()               ← CLI flag → TTY prompt → env var
          │
          ▼
_prompt_password_if_needed()         ← masked getpass if password auth + no env var
          │
          ▼
Startup banner printed               ← auth method + inactivity timeout displayed
          │
          ▼
_reset_inactivity_timer()            ← threading.Timer starts (default 1 hour)
          │
          ▼
[disconnected] prompt
          │
          │  User: "Connect to the host"
          ▼  _reset_inactivity_timer() resets on every user turn
boundary_session.authenticate()      ← get BOUNDARY_TOKEN
          │
          ▼
boundary_session.connect(target_id)  ← spawn proxy, parse JSON, extract creds
          │
          ▼
ssh_exec.connect(host, port, ...)    ← Transport + PTY + shell
          │
          ▼
[connected ✓] prompt
          │
          │  User: "Show me disk usage"
          ▼
ssh_exec.run_command("df -h /")      ← reuses existing shell channel
          │
          ▼
[connected ✓] prompt  (N more commands, same shell)
          │
          │  No user input for INACTIVITY_TIMEOUT_SECONDS
          ▼  (runs in background thread)
ssh_exec.disconnect()                ← auto-disconnect on inactivity timeout
boundary_session.disconnect()        ← terminate proxy process
          │
          ▼  (or: User: "Disconnect" triggers the same two calls)
[disconnected] prompt
```

---

## Authentication Paths

### Current: Password Auth

```
.env
  BOUNDARY_AUTH_METHOD=password
  BOUNDARY_AUTH_METHOD_ID=ampw_...
  BOUNDARY_LOGIN_NAME=mock-ai-agent
  BOUNDARY_PASSWORD=...
          │
          ▼
boundary authenticate password
  -password=env://_BOUNDARY_PASSWORD   ← CLI v0.19+ requires env:// or file://
          │
          ▼
BOUNDARY_TOKEN stored in os.environ
```

### Planned: OIDC via IBM Verify

```
.env
  BOUNDARY_AUTH_METHOD=oidc
  BOUNDARY_OIDC_AUTH_METHOD_ID=amoidc_...
          │
          ▼
boundary authenticate oidc
  -auth-method-id=amoidc_...
          │
          ▼
Browser opens → IBM Verify login → MFA → token returned
          │
          ▼
BOUNDARY_TOKEN stored in os.environ
```

No code changes required to switch — only the `.env` values change.

---

## Credential Injection

Boundary's credential injection means the agent **never holds the SSH password statically**:

```
Boundary Credential Store
  (SSH username + password stored in HCP Boundary)
          │
          ▼
boundary connect -target-id=tssh_...
          │
          ▼  JSON response includes:
  "credentials": [{"credential": {"username": "...", "password": "..."}}]
          │
          ▼
ssh_exec.connect(..., password=injected_password)
          │
          ▼
paramiko.Transport.auth_none()        ← proxy accepts, no creds needed
   or .auth_password()               ← fallback if proxy requires it
```

The password is passed ephemerally at connect time and is never written to disk, logged, or included in any LLM context.

---

## Data Flow: A Single Command Turn

```
User input: "What is the current memory usage?"
          │
          ▼
agent.run(user_message, history)
          │
          ▼
watsonx_llm.chat(messages)
  → {"action": "run_command", "args": {"command": "free -h"}}
          │
          ▼
execute_tool("run_command", {"command": "free -h"})
          │
          ▼
ssh_exec.run_command("free -h")
  → shell.sendall("free -h ; echo __Xabc123__$?\n")
  → read until prompt
  → parse: stdout="...", exit_code=0
          │
          ▼
observation = "exit_code=0\nstdout:\n              total   used   free\nMem:  8.0Gi  3.5Gi  4.5Gi"
          │
          ▼
watsonx_llm.chat(messages + observation)
  → {"action": "final_answer", "answer": "Memory usage: 3.5 GB used of 8 GB total, 4.5 GB free."}
          │
          ▼
main.py prints: "Agent: Memory usage: 3.5 GB used of 8 GB total, 4.5 GB free."
```

---

## Project Structure

```
ai-boundary-agent/
├── src/
│   └── agent/
│       ├── __init__.py
│       ├── agent.py             # ReAct loop, tool definitions, tool executor
│       ├── boundary_session.py  # Boundary auth + proxy session lifecycle
│       ├── ssh_exec.py          # paramiko Transport + persistent PTY shell
│       └── watsonx_llm.py       # WatsonX.ai SDK wrapper (chat API)
├── tests/
│   ├── test_agent.py            # (planned)
│   ├── test_boundary_session.py # Offline unit tests — mocked subprocess
│   ├── test_ssh_exec.py         # Offline unit tests — mocked Transport/Channel
│   └── test_watsonx_llm.py      # Offline unit tests — mocked ModelInference
├── main.py                      # Terminal REPL entry point
├── .env.example                 # Credential template (placeholders only)
├── requirements.txt
├── README.md
├── ARCHITECTURE.md              # This document
└── BLOG.md                      # Technical blog post
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| **Persistent shell over exec_command** | Preserves `cwd`, env vars, and shell state between commands. Boundary session recording captures one continuous interactive session rather than discrete exec calls. |
| **`auth_none` for SSH** | The Boundary proxy handles credential injection at the Worker layer — it accepts anonymous SSH auth. Standard `SSHClient` tries password/key auth by default and fails. |
| **Sentinel-based output delimiting** | Shell prompts are unreliable output terminators because their shape varies. A unique UUID sentinel echoed with the exit code gives a deterministic end-of-output marker independent of prompt format. |
| **`PROMPT_COMMAND=''` after connect** | Cloud VM images commonly use `PROMPT_COMMAND` to print instance metadata before every prompt. This contaminates the LLM's input. Clearing it at connect time keeps output clean. |
| **`boundary connect` not `boundary connect ssh`** | `boundary connect` (raw TCP proxy) emits the proxy address/port as JSON and stays alive. `boundary connect ssh` launches an SSH client directly — not suitable for programmatic use with paramiko. |
| **`env://` for Boundary password** | CLI v0.19+ rejects plain-string `--password` values. Passing via a temporary env var satisfies the security requirement without writing the password to a file. |
| **SDK `model.chat()` not `generate_text()`** | The chat API handles prompt formatting per-model automatically. `generate_text()` requires manually constructing model-specific chat templates and is deprecated. |
| **`threading.Timer` for inactivity timeout** | An open Boundary session consumes a license seat and remains visible in the audit log. A daemon timer that fires on inactivity automatically releases the session without requiring the user to remember to disconnect. Daemon mode ensures the timer never blocks Python from exiting. |
| **`readline` imported for side-effect** | Importing `readline` unconditionally activates arrow-key navigation and command history in `input()` on POSIX platforms — no configuration needed. |
