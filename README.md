# WatsonX + HCP Boundary AI Agent

A WatsonX.ai-powered agent that connects to a remote Ubuntu host through **HCP Boundary** and executes shell commands — available in two modes:

| Mode | Entry point | How it works |
|------|-------------|-------------|
| **Conversational** | `main.py` | Terminal REPL — you drive each turn interactively |
| **Autonomous** | `main_autonomous.py` | Give it a goal — it plans, executes, and reports on its own |

Both modes share the same underlying modules: `boundary_session.py`, `ssh_exec.py`, and `watsonx_llm.py`.

---

## Architecture

### Conversational agent (`main.py`)
```
main.py (chat REPL)
  └─▶ agent.py (ReAct loop — IBM Granite via WatsonX.ai)
        ├─▶ connect_to_host       → boundary_session.py + ssh_exec.py  [opens once]
        ├─▶ run_command           → ssh_exec.run_command()              [reuses conn ×N]
        ├─▶ check_connection_status
        └─▶ disconnect_from_host → ssh_exec + boundary_session          [explicit only]
```

### Autonomous agent (`main_autonomous.py`)
```
main_autonomous.py
  └─▶ agent_autonomous.py
        ├─▶ plan()        — LLM decomposes GOAL into ordered sub-tasks
        ├─▶ execute()     — ReAct loop per sub-task (same tools as conversational)
        └─▶ evaluate()    — LLM confirms whether the GOAL was fully achieved
```

**Session model:** The agent connects once. All `run_command` calls reuse the same persistent SSH connection through the Boundary proxy tunnel.

**Credential injection:** The Ubuntu SSH password is stored in the HCP Boundary credential store. The agent never handles the SSH password — Boundary injects it at the proxy layer.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | |
| `boundary` CLI | [Download](https://developer.hashicorp.com/boundary/install) — version must match your HCP Boundary cluster |
| HCP Boundary account | SSH target configured with credential injection (Ubuntu password stored in credential store) |
| WatsonX.ai access | IBM Cloud account with a WatsonX project and API key |

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <repo-url>
cd ai-boundary-agent
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure credentials

```bash
cp .env.example .env
# Edit .env with your real values
```

---

## Running the Conversational Agent

Open `ai-boundary-agent-conversational.code-workspace` in VS Code, or run directly:

```bash
python main.py
python main.py --auth oidc
```

```
[disconnected] You: Connect to the host and show me disk usage
Agent: Connected. Disk usage:
  Filesystem      Size  Used Avail Use% Mounted on
  /dev/sda1        50G   12G   36G  25% /

[connected ✓] You: Now check available memory
Agent: Available memory: 3.2 GB free out of 8 GB total.

[connected ✓] You: Disconnect
Agent: Disconnected.
```

---

## Running the Autonomous Agent

Open `ai-boundary-agent-autonomous.code-workspace` in VS Code, or run directly:

```bash
python main_autonomous.py "Audit disk usage, memory, and top CPU processes, then report"
python main_autonomous.py --auth oidc "Check if nginx is running and report its status"
```

The agent prints its plan before executing, then shows each sub-task result as it completes:

```
──────────── WatsonX + HCP Boundary Autonomous Agent ────────────
Auth: password · Mode: autonomous

Goal: Audit disk usage, memory, and top CPU processes, then report

──────────────────────────── Plan ───────────────────────────────
  1. Connect to the host
  2. Check disk usage on all mount points
  3. Check available memory
  4. List top 5 CPU-consuming processes
  5. Disconnect from the host

──────────── Step 1 — Connect to the host ───────────────────────
Connected. Boundary session active. SSH connected as mock-ai-agent-linux.

──────────── Step 2 — Check disk usage on all mount points ──────
/dev/sda1: 50G total, 12G used (25%), 36G free.

... (steps 3–5) ...

──────────────────────────── Summary ────────────────────────────
 Sub-task                              Result
 Connect to the host                   Connected successfully.
 Check disk usage on all mount points  /dev/sda1: 25% used, 36G free.
 Check available memory                3.2 GB free of 8 GB total.
 List top 5 CPU-consuming processes    python3 12.4%, nginx 3.1%, ...
 Disconnect from the host              Disconnected.

✓ Goal achieved: All audit tasks completed successfully.
```

---

## Switching to OIDC Authentication

```dotenv
BOUNDARY_AUTH_METHOD=oidc
BOUNDARY_OIDC_AUTH_METHOD_ID=amoidc_<your-oidc-method-id>
```

A browser window will open for SSO login. No code changes required.

---

## Running Tests

```bash
pytest tests/ -v
```

All tests are offline — no live Boundary or WatsonX calls required.

---

## Project Structure

```
ai-boundary-agent/
├── src/
│   └── agent/
│       ├── watsonx_llm.py            # WatsonX.ai IBM Granite wrapper       [shared]
│       ├── boundary_session.py       # HCP Boundary session lifecycle        [shared]
│       ├── ssh_exec.py               # Persistent paramiko SSH connection    [shared]
│       ├── agent.py                  # Conversational ReAct loop
│       └── agent_autonomous.py       # Autonomous plan→execute→evaluate loop
├── tests/
│   ├── test_watsonx_llm.py
│   ├── test_boundary_session.py
│   └── test_ssh_exec.py
├── main.py                           # Conversational REPL entry point
├── main_autonomous.py                # Autonomous agent entry point
├── ai-boundary-agent-conversational.code-workspace
├── ai-boundary-agent-autonomous.code-workspace
├── .env.example
├── requirements.txt
├── ARCHITECTURE.md
└── README.md
```

---

## Branch Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Conversational agent — stable, production-ready |
| `autonomous-agent` | Autonomous agent development — `agent_autonomous.py` + `main_autonomous.py` |
