# Autonomous SRE: How I Connected an AI Agent to a Live EC2 Instance Through Zero-Trust Infrastructure Access

## Rethinking infrastructure operations with IBM WatsonX.ai, IBM Granite, and HCP Boundary

---

Platform engineers and SREs share a quiet frustration: the gap between the question and the answer is enormous. Someone asks "is this host healthy?" and what follows is not an answer — it is a ritual. Open a terminal. Look up the hostname. Find credentials. Verify permissions. Run five commands. Interpret the output. Repeat for the next metric.

This project was born from that frustration. The goal: build an autonomous AI agent that can receive a plain-English goal, plan a sequence of infrastructure tasks, execute them against a live EC2 instance, and report back — all through a secure, audited, zero-trust session. No VPN. No standing SSH access. No credentials on disk.

This post covers the architecture, the security and compliance model, and the SRE use cases the agent can handle today.

---

## Prerequisites

To follow along and run this project yourself, you will need accounts and access to the following platforms. All of them offer free tiers sufficient for this demo.

### 1. IBM Cloud — WatsonX.ai

The agent's reasoning engine. IBM Granite models are served through the WatsonX.ai inference API on IBM Cloud.

| What you need | Details |
|---|---|
| **IBM Cloud account** | Free tier (Lite) is sufficient to get started |
| **WatsonX.ai project** | Create a project in the WatsonX.ai console to obtain a Project ID |
| **API key** | Generate an IBM Cloud API key with access to your WatsonX project |
| **Model** | `ibm/granite-3-1-8b-instruct` — available on the free tier |

**Sign up:** [https://cloud.ibm.com/registration](https://cloud.ibm.com/registration)
**WatsonX.ai console:** [https://dataplatform.cloud.ibm.com/wx/home](https://dataplatform.cloud.ibm.com/wx/home)
**API key management:** [https://cloud.ibm.com/iam/apikeys](https://cloud.ibm.com/iam/apikeys)

---

### 2. HashiCorp Cloud Platform (HCP) — Boundary

The zero-trust session broker. HCP Boundary manages all authentication, credential injection, and session recording — the agent never touches the SSH key or password directly.

| What you need | Details |
|---|---|
| **HCP account** | Free tier available — includes one Boundary cluster |
| **HCP Boundary cluster** | Create a cluster in the HCP portal; note the cluster URL |
| **SSH target** | Register your EC2 instance as an SSH target inside Boundary |
| **Credential store** | Add a static credential store containing the Ubuntu SSH password |
| **Boundary CLI** | Install locally — version must match your HCP Boundary cluster |

**Sign up:** [https://portal.cloud.hashicorp.com/sign-up](https://portal.cloud.hashicorp.com/sign-up)
**HCP Boundary docs:** [https://developer.hashicorp.com/hcp/docs/boundary](https://developer.hashicorp.com/hcp/docs/boundary)
**Boundary CLI install:** [https://developer.hashicorp.com/boundary/install](https://developer.hashicorp.com/boundary/install)

> **Tip:** The HCP free tier includes a Boundary Plus trial. You do not need a paid plan to run this demo. Session recording is available on the Plus tier — activate the trial to use it.

---

### 3. AWS — EC2 Instance (Ubuntu 22.04)

The remote host the agent connects to and runs commands on. The project has been tested on Ubuntu 22.04 LTS.

| What you need | Details |
|---|---|
| **AWS account** | Free tier eligible — `t3.small` is the minimum for the demo scenarios |
| **EC2 instance** | Launch an Ubuntu 22.04 LTS instance (Canonical official AMI) |
| **Security group** | Port 22 open **only to your HCP Boundary Worker IPs** — not to the internet |
| **SSH key pair** | Required for the initial launch; after Boundary is configured, the agent uses credential injection instead |

**Sign up:** [https://aws.amazon.com/free](https://aws.amazon.com/free)
**EC2 console:** [https://console.aws.amazon.com/ec2](https://console.aws.amazon.com/ec2)
**Ubuntu 22.04 AMI finder:** [https://cloud-images.ubuntu.com/locator/ec2/](https://cloud-images.ubuntu.com/locator/ec2/)

> **Note:** The `t3.small` instance type (2 vCPU, 2 GB RAM) is sufficient for all 13 SRE scenarios. The `t2.micro` free-tier instance will work for basic health checks but may struggle with `stress-ng` load scenarios.

---

### 4. Python 3.10+ (local machine)

The agent runs as a Python process on your developer machine.

| Dependency | Version | Purpose |
|---|---|---|
| `ibm-watsonx-ai` | ≥ 1.0.0 | WatsonX.ai SDK — LLM calls to IBM Granite |
| `paramiko` | ≥ 3.4.0 | SSH client — connects through the Boundary proxy tunnel |
| `python-dotenv` | ≥ 1.0.0 | Loads `.env` credentials at startup |
| `rich` | ≥ 13.7.0 | Terminal output formatting |

**Download Python:** [https://www.python.org/downloads/](https://www.python.org/downloads/)

All Python dependencies install in one command: `pip install -r requirements.txt`

---

### Optional: IBM Verify (for OIDC authentication)

If you want to use SSO / MFA for Boundary authentication instead of a username and password — which is the recommended approach for any shared environment — you can configure an OIDC auth method in Boundary backed by IBM Verify (or any OIDC-compatible identity provider such as Okta, Azure AD, or Google).

**IBM Verify trial:** [https://www.ibm.com/products/verify-identity/trial](https://www.ibm.com/products/verify-identity/trial)
**Boundary OIDC auth method docs:** [https://developer.hashicorp.com/boundary/docs/configuration/identity-and-access-management/oidc-auth-method](https://developer.hashicorp.com/boundary/docs/configuration/identity-and-access-management/oidc-auth-method)

---

### Setup checklist

Before running the agent, confirm:

- [ ] IBM Cloud account created; WatsonX.ai project exists with a Project ID
- [ ] IBM Cloud API key generated and saved
- [ ] HCP account created; Boundary cluster provisioned and URL noted
- [ ] Boundary CLI installed locally at the correct version
- [ ] EC2 instance running Ubuntu 22.04; reachable by your Boundary Worker
- [ ] EC2 instance registered as an SSH target in Boundary with credential injection configured
- [ ] `.env` populated from `.env.example` with all required values
- [ ] `pip install -r requirements.txt` completed successfully
- [ ] (For demo scenarios) `ec2-demo-setup.sh` run on the EC2 instance

---

## The Architecture

The system is composed of four modules, each with a single responsibility:

```
main_autonomous.py  (entry point — goal input, rich terminal output)
  └─▶ agent_autonomous.py  (Plan → Execute → Evaluate loop)
        ├─▶ watsonx_llm.py        (IBM Granite via WatsonX.ai)
        ├─▶ boundary_session.py   (HCP Boundary auth + proxy tunnel)
        └─▶ ssh_exec.py           (persistent paramiko SSH shell)
```

```
Developer Machine
┌──────────────────────────────────────────────────────┐
│                                                      │
│  main_autonomous.py ──▶ agent_autonomous.py          │
│                              │                       │
│                    ┌─────────┼─────────┐             │
│                    ▼         ▼         ▼             │
│              watsonx_llm  boundary_  ssh_exec        │
│                           session                    │
└──────────────────────────────────────────────────────┘
         │ HTTPS                    │ SSH-over-TCP
         ▼                          ▼
  WatsonX.ai API            HCP Boundary Cluster
  (IBM Cloud)               (HashiCorp Cloud)
                                    │
                                    │ SSH (credential injected)
                                    ▼
                            Ubuntu EC2 Instance
```

### The autonomous loop

The agent operates in three phases for every goal:

**Phase 1 — Plan.** The goal string is sent to IBM Granite with a system prompt instructing it to decompose the goal into an ordered list of concrete sub-tasks. The output is a JSON array of strings — specific, actionable, independently executable.

**Phase 2 — Execute.** Each sub-task runs through a ReAct (Reason + Act) loop. The model receives the sub-task and the available tools, decides which command to run, receives the output as an observation, and continues until it produces a final answer for that sub-task. Results accumulate across sub-tasks so each step has context from the previous ones.

**Phase 3 — Evaluate.** After all sub-tasks complete, the model reviews the original goal and the full set of results to determine whether the goal was achieved. It returns a boolean and a plain-English explanation.

The result is an agent that behaves like a junior SRE being handed a ticket: it reads the goal, figures out the steps, executes them methodically, and writes up a summary.

---

## The Security Model

This is the part that separates this approach from simply giving an AI agent an SSH key and a shell.

### Zero-trust access via HCP Boundary

HCP Boundary implements zero-trust network access for infrastructure. Rather than granting the agent direct SSH access to the EC2 instance — which would require a persistent network path, firewall rules, and static credentials — every connection is brokered through Boundary.

The flow works as follows:

1. The agent authenticates to Boundary using either a username/password or OIDC (browser-based SSO via IBM Verify).
2. Boundary issues a short-lived session token.
3. The agent calls `boundary connect` with the target ID, and Boundary opens a local TCP proxy tunnel on `127.0.0.1:<random-port>`.
4. The agent connects paramiko to that local port. Boundary's Worker node proxies the SSH traffic to the EC2 instance.
5. When the session ends, the tunnel closes and the token expires. There is no persistent network path back to the EC2 instance.

This means:

- **No VPN required.** The EC2 instance does not need to be on a shared network with the developer machine.
- **No standing SSH access.** The agent cannot reach the EC2 instance except through an active, authenticated Boundary session.
- **No firewall exceptions.** Port 22 on the EC2 instance is not exposed to the internet — only to Boundary Workers.

### Credential injection — the agent never holds the SSH password

This is the most important security property of the design. The SSH password for the Ubuntu user is stored in a **Boundary credential store** — not in the agent's code, not in `.env`, not anywhere on the developer machine.

When the agent calls `boundary connect`, the JSON response includes the injected credentials:

```json
{
  "address": "127.0.0.1",
  "port": 49220,
  "session_id": "s_abc123",
  "credentials": [
    {"credential": {"username": "ubuntu", "password": "<ephemeral>"}}
  ]
}
```

The agent extracts this password, uses it for the SSH handshake, and then it is gone — never written to disk, never logged, never included in any LLM context. Every session gets a fresh ephemeral credential from Boundary.

The blast radius of a compromised developer machine is dramatically reduced: there is no SSH key or password to steal. An attacker would need to compromise both the developer machine and an active Boundary session simultaneously.

### Prompt-level safety rules

The agent carries a hard-coded set of safety rules in its system prompt that are active on every turn:

- **No destructive commands:** `rm -rf`, `dd`, `mkfs`, `shutdown`, `reboot`, `poweroff` are explicitly forbidden.
- **No credential changes:** `useradd`, `userdel`, `passwd` are off-limits.
- **No firewall modifications:** `iptables`, `ufw`, `nftables` are excluded.
- **No raw device writes:** Writing to block devices (`> /dev/sda`) is prohibited.
- **Default to caution:** If uncertain whether a command is safe, the agent stops and asks rather than proceeding.

IBM Granite and Llama models follow these negative constraints reliably across a wide range of instructions.

The honest caveat: prompt-level rules are a soft guard. For production use, a code-level blocklist checked before any command reaches the SSH layer is the appropriate enforcement point. For a dev/demo environment, the prompt rules are sufficient.

### Admin-initiated session cancellation

One of the most important properties of running an AI agent through a zero-trust session broker is that **a human is always in control**. A Boundary admin can open the HCP portal at any point during an agent run, locate the active session, and cancel it. When that happens, Boundary terminates the proxy tunnel immediately.

The agent is designed to detect this and shut down gracefully rather than hanging or producing a confusing error. On every tool call — before sending a single command to the remote host — the agent checks whether the `boundary connect` process is still alive. If Boundary has cancelled the session, the process will have exited. The agent raises a `BoundarySessionCancelledError`, stops the run immediately, and prints a clear message to the operator:

```
══════════════════ Session Cancelled ═════════════════════
⊘ Boundary session has been cancelled externally by the admin.
The agent has exited. No further commands will be executed.
```

No partial results are silently discarded, no commands are retried on a dead tunnel, and no misleading error stack trace is shown. The operator knows exactly what happened and why.

This matters for two reasons. First, it gives security teams a hard kill switch. If an agent run is producing unexpected behaviour — if the LLM is about to run a command that should not be run, or if the wrong host was targeted — an admin can stop the session from the Boundary control plane in seconds, from anywhere, without needing access to the terminal running the agent. Second, it makes the agent safe to run in environments where session duration policies are enforced. If a policy limits sessions to 30 minutes and the agent is still running at minute 31, it will exit cleanly rather than failing in an uncontrolled way.

The check adds no latency to normal operation: `poll()` on a running process returns immediately. It is called once per tool invocation, which typically means once per shell command the agent issues.

### Inactivity timeout

An open Boundary session consumes a license seat and remains visible in the audit log as an active session. The agent automatically disconnects after a configurable period of inactivity (default: 1 hour). This is implemented as a `threading.Timer` that resets on every user interaction and fires `ssh_exec.disconnect()` + `boundary_session.disconnect()` if no activity is detected.

---

## Compliance and Audit

### Every session is recorded

Boundary records the entire agent conversation as a **single continuous interactive session** in its admin console. This is a deliberate design choice: the agent uses a persistent PTY shell rather than per-command `exec_command()` calls. The result is that a Boundary session recording shows one coherent session — every command the agent ran, every response it received — just as if a human operator had been at the keyboard.

This gives compliance and audit teams:

- **Who** authenticated (Boundary identity — service account or human operator via OIDC)
- **When** the session started and ended
- **What** commands were executed, in sequence
- **Which** target was accessed

All of this is in the Boundary admin console without any additional instrumentation.

### OIDC authentication via IBM Verify

For human-operator sessions, the agent supports OIDC authentication through IBM Verify. The operator runs the agent with `--auth oidc`, a browser window opens to the IBM Verify SSO page, and after MFA the agent receives a short-lived session token. No password is typed into the terminal. IBM Verify enforces session duration, MFA policy, and group-based access control — the agent code has no knowledge of any of this.

This means the access model for the agent is identical to the access model for a human operator: authenticate through the corporate IdP, get a time-limited token, access only the targets you are authorised for.

### Password authentication with retry protection

For automated or service-account use, password authentication is available. The implementation enforces:

- Passwords passed to the Boundary CLI via a temporary environment variable (`env://`) — not as a command-line argument, which would appear in process listings.
- Up to 3 retry attempts if the password is wrong, with a clear remaining-attempts counter.
- Automatic lockout and error after 3 failures.
- Masked input via `getpass` so the password never appears on screen.

---

## SRE Use Cases

The agent ships with 13 named scenarios across three categories. Each is a complete goal string that the autonomous planner knows how to decompose into sub-tasks.

### Observability and health checks

| Scenario | What the agent does |
|---|---|
| `health-check` | CPU load average, memory pressure, disk usage across all mounts, top 5 processes |
| `zombie-processes` | Finds zombie and D-state processes, reports PIDs, names, and parent processes |
| `io-performance` | Disk I/O throughput, top I/O-consuming processes, processes in I/O wait |

### Incident triage

| Scenario | What the agent does |
|---|---|
| `disk-triage` | Top 10 largest files and directories, partitions over 80%, recently created large files |
| `memory-leak-triage` | Top 5 processes by RSS and VSZ, OOM kill threshold check |
| `auth-audit` | Last 10 failed login attempts, currently logged-in users, recent sudo usage |
| `high-load-triage` | Top CPU processes, load average trend, kernel messages, vmstat output |

### Platform and infrastructure operations

| Scenario | What the agent does |
|---|---|
| `failed-services` | All failed/degraded systemd units, last error per unit, restart counts |
| `system-inventory` | OS version, kernel, CPU model, memory, disk layout, package count, uptime |
| `cron-audit` | User crontabs, system cron jobs, last 10 cron daemon log entries |
| `log-rotation-audit` | Logrotate config, log file sizes, files over 50 MB, last rotation date |
| `onboarding-audit` | Non-system users, sudo permissions, SSH authorized_keys, last login times |

Running any scenario is a single command:

```bash
python main_autonomous.py --scenario health-check --auth oidc
```

The agent prints its plan before executing, streams results as each sub-task completes, and produces a summary table at the end. The entire run — plan, execution, results — is captured in the Boundary session recording.

---

## A Concrete Example: System Health Check

Given the goal `"Run a full system health check: CPU load average, memory pressure, disk usage across all mount points, and the top 5 resource-consuming processes"`, the planner produces:

```
Plan:
  1. Connect to the host
  2. Check CPU load average and system uptime
  3. Check memory pressure and usage
  4. Check disk usage across all mount points
  5. List the top 5 resource-consuming processes
  6. Disconnect from the host
```

The executor runs each sub-task through its ReAct loop, producing observations like:

```
Step 2 — Check CPU load average and system uptime
Load average: 0.08 (1 min), 0.12 (5 min), 0.10 (15 min).
Uptime: 14 days, 3 hours. System is idle.

Step 3 — Check memory pressure and usage
Total: 7.7 GB. Used: 2.1 GB. Free: 4.3 GB. Buffers/cache: 1.3 GB.
No memory pressure detected.

Step 4 — Check disk usage across all mount points
/dev/root: 7.6G total, 3.2G used (42%). Remaining capacity: 4.5G.
No partitions over 80%.

Step 5 — List top 5 resource-consuming processes
1. python3    2.1% CPU  312 MB RSS
2. sshd       0.3% CPU   12 MB RSS
3. systemd    0.1% CPU   18 MB RSS
4. cron       0.0% CPU    4 MB RSS
5. rsyslog    0.0% CPU    6 MB RSS
```

The evaluator then confirms the goal was achieved and the session closes. Total elapsed time: under 60 seconds. Boundary session recording: a complete audit trail of every command.

---

## What This Enables

The shift from manual to autonomous infrastructure operations changes the economics of platform engineering in a few concrete ways.

**Incident triage becomes a goal, not a procedure.** Instead of an on-call engineer running 10 commands from memory at 2 AM, they type a single sentence. The agent handles the command sequence, the interpretation, and the summary. The engineer reviews the output and decides what to do next.

**Audit compliance is automatic.** Every agent session produces a Boundary recording with the same fidelity as a human operator session. Compliance teams get a continuous, tamper-evident record without any additional tooling.

**Credential management is eliminated.** SSH passwords live in Boundary, not in team vaults, `.env` files, or engineer laptops. Rotating the password on the EC2 instance requires updating one Boundary credential store entry — the agent picks up the new credential on the next session automatically.

**Admins retain a hard kill switch.** The agent is not an autonomous process that runs beyond human control. A Boundary admin can cancel any active session from the control plane at any moment. The agent detects this immediately, stops cleanly, and reports what happened. This is the difference between AI-assisted operations and AI-controlled operations — the human is always one click away from stopping it.

**SRE onboarding accelerates.** A new engineer who does not know the team's Linux conventions can run `--scenario onboarding-audit` or `--scenario system-inventory` and get a complete picture of a host in under a minute. The agent bridges the gap between natural language and shell expertise.

---

## What Comes Next

The current implementation is a working prototype that demonstrates the core pattern. Three natural extensions would make it production-ready:

**HashiCorp Vault for remaining secrets.** The WatsonX API key and Boundary authentication credentials are currently in `.env`. The right long-term home is Vault — fetched dynamically at startup, short-lived, rotated automatically. HCP Vault Secrets sits alongside HCP Boundary in the same HashiCorp Cloud portal.

**Multi-target fleet support.** The agent connects to a single target per session. A fleet mode would allow comparative queries across groups of hosts — "find the host in this group with the highest memory pressure" — and anomaly detection at scale.

**Web UI with session replay.** The terminal interface works well for engineers comfortable with the CLI. A React front-end with Boundary session replay integration would make the tool accessible to operations managers and compliance reviewers who need the audit trail but not the terminal.

---

## The Bigger Picture

What makes this project interesting is not any single component. IBM Granite provides the reasoning. HCP Boundary provides the zero-trust access model and the audit trail. The persistent shell gives the agent the same contextual continuity a human operator has. Together they produce something that behaves like a careful, methodical SRE — one that never gets tired, never skips the audit log entry, and never stores a password on disk.

The pieces to build this exist today, available on free tiers, and composable with a few hundred lines of Python. The direction infrastructure tooling is heading is clear. The question is how quickly teams adopt it.

---

*The full source code, tests, and documentation are in the `ai-boundary-agent` repository. Run `python main_autonomous.py --list-scenarios` to see all available SRE scenarios. Run `python main_autonomous.py --scenario health-check --auth oidc` to try the first one.*
