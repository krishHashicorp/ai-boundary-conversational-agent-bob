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

## EC2 Demo Setup

Before running the SRE scenarios, prepare your Ubuntu 22.04 EC2 instance with the included setup script. It installs all required packages, seeds demo data, and creates background services that make the scenarios realistic.

```bash
# Copy to your EC2 instance and run once
scp ec2-demo-setup.sh ubuntu@<your-ec2-ip>:~
ssh ubuntu@<your-ec2-ip> "bash ec2-demo-setup.sh"
```

**What the script installs and configures:**

| Package / Resource | Purpose |
|---|---|
| `nginx` | Web server — required for `service-triage` and `log-rotation-audit` |
| `sysstat` | Provides `iostat` / `sar` — required for `io-performance` |
| `stress-ng` | CPU/memory load generator — used in `high-load-triage` |
| `fail2ban` | Seeds auth failure log entries — used in `auth-audit` |
| Demo cron jobs | User crontab + `/etc/cron.d/demo-cleanup` — used in `cron-audit` |
| 512 MB dummy file | `/var/lib/demo-large-file.bin` — used in `disk-triage` |
| `demo-zombie.service` | Persistent zombie process — used in `zombie-processes` |

**Minimum instance:** `t3.small` (2 vCPU, 2 GB RAM). Re-running the script is safe — all steps are idempotent.

---

## Running the Autonomous Agent

Open `ai-boundary-agent-autonomous.code-workspace` in VS Code, or run directly:

```bash
# Free-form goal
python main_autonomous.py "Audit disk usage, memory, and top CPU processes, then report"

# Named SRE scenario
python main_autonomous.py --scenario health-check
python main_autonomous.py --scenario service-triage --auth oidc

# List all available scenarios
python main_autonomous.py --list-scenarios
```

The agent prints its plan before executing, then shows each sub-task result as it completes:

```
──────────── WatsonX + HCP Boundary Autonomous Agent ────────────
Auth: password · Mode: autonomous · Scenario: Full System Health Check

Goal: Run a full system health check: report CPU load average, memory pressure,
disk usage across all mount points, and the top 5 resource-consuming processes

──────────────────────────── Plan ───────────────────────────────
  1. Connect to the host
  2. Check CPU load average and memory pressure
  3. Check disk usage across all mount points
  4. List the top 5 resource-consuming processes
  5. Disconnect from the host

──────────── Step 1 — Connect to the host ───────────────────────
Connected. Boundary session active. SSH connected as mock-ai-agent-linux.

──────────── Step 2 — Check CPU load average and memory pressure ─
Load average: 0.12 (1 min). Memory: 3.5 GB used of 8 GB total, 4.5 GB free.

... (steps 3–5) ...

──────────────────────────── Summary ────────────────────────────
 Sub-task                              Result
 Connect to the host                   Connected successfully.
 Check CPU load and memory pressure    Load: 0.12. Memory: 4.5 GB free.
 Check disk usage                      /dev/sda1: 25% used, 36G free.
 Top 5 resource-consuming processes    python3 12.4%, nginx 3.1%, ...
 Disconnect from the host              Disconnected.

✓ Goal achieved: All health check tasks completed successfully.
```

---

## SRE Scenario Reference

Run `python main_autonomous.py --list-scenarios` for the full interactive list. All 13 named scenarios:

### Observability & Health Checks

| Key | Name | Prerequisites |
|---|---|---|
| `health-check` | Full System Health Check | None |
| `zombie-processes` | Zombie & D-State Process Hunt | `ec2-demo-setup.sh` (demo-zombie service) |
| `io-performance` | Disk I/O Performance Analysis | `ec2-demo-setup.sh` (sysstat) |

### Incident Triage

| Key | Name | Prerequisites |
|---|---|---|
| `disk-triage` | Disk Usage Triage | `ec2-demo-setup.sh` (512 MB dummy file) |
| `memory-leak-triage` | Memory Leak Triage | None |
| `auth-audit` | Authentication Security Audit | `ec2-demo-setup.sh` (fail2ban) |
| `service-triage` | Nginx Service Triage | `ec2-demo-setup.sh` (nginx) |
| `high-load-triage` | High CPU Load Triage | `ec2-demo-setup.sh` (stress-ng, optional) |

### Platform / Infrastructure Ops

| Key | Name | Prerequisites |
|---|---|---|
| `failed-services` | Failed & Degraded Services Audit | None |
| `system-inventory` | System Inventory Report | None |
| `cron-audit` | Scheduled Jobs Audit | `ec2-demo-setup.sh` (demo cron entries) |
| `log-rotation-audit` | Log Rotation Audit | `ec2-demo-setup.sh` (nginx + log entries) |
| `onboarding-audit` | SRE Onboarding Audit | None |

### Nginx Operations

| Key | Name | Prerequisites |
|---|---|---|
| `nginx-access-analysis` | Nginx Traffic Pattern Analysis | `ec2-demo-setup.sh` (nginx + seeded logs) |
| `nginx-config-audit` | Nginx Configuration Audit | nginx installed and running |
| `nginx-incident-response` | Nginx Incident Response | nginx installed; optionally stop nginx first for a fault demo |

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
