# Building a Conversational AI Agent for Secure Infrastructure Access
## How WatsonX.ai, IBM Granite, and HCP Boundary came together into a working agent

---

There is a pattern that comes up repeatedly in platform engineering: someone needs to inspect a server, check disk usage, or investigate a running process — and to do that they have to open a terminal, remember a hostname, look up credentials, and hope they have the right permissions. It works, but it is not elegant, and it does not scale. The question that kicked off this project was simple: what if you could just ask a question in plain English and let an agent handle the rest — securely?

That question turned into a working prototype in a single session. This is the story of how it was built.

---

## The Plan

The starting point was identifying the right combination of tools. Three were chosen deliberately.

**IBM Granite via WatsonX.ai** provides the language reasoning layer. Granite is an instruction-following model that can be told to respond only in structured JSON, which makes it well-suited to driving a tool-use loop rather than generating free-form prose.

**HCP Boundary** handles the access layer. Rather than giving the agent direct network access or storing SSH credentials in the application, Boundary acts as the broker — it authenticates the agent, authorises access to specific targets, and injects SSH credentials at the proxy layer. The agent never sees the Ubuntu password.

**Python with paramiko** handles the execution layer — the actual SSH connection to the remote Ubuntu host, running commands, and returning output.

The architecture that fell out of this was straightforward:

```
main.py  (terminal chat REPL)
  └─▶ agent.py  (ReAct reasoning loop)
        ├─▶ boundary_session.py  (Boundary authentication + proxy tunnel)
        ├─▶ ssh_exec.py          (persistent SSH shell)
        └─▶ watsonx_llm.py       (IBM Granite via WatsonX.ai)
```

The key design decision was the session model: **connect once, reuse indefinitely**. The agent opens a Boundary session and an SSH shell at the start of a conversation, then all subsequent commands run inside that same shell — preserving working directory, environment variables, and any shell state between turns. The session is only torn down when the user explicitly asks to disconnect — or automatically, after a configurable inactivity timeout.

---

## The ReAct Loop

The agent's reasoning engine is built on the **ReAct pattern** (Reason + Act). At each step the model receives the conversation history and a JSON schema describing the available tools. It responds with a single JSON object — either a tool call or a final answer. The tool result is fed back as an observation, and the cycle continues until the model produces a `final_answer` or hits the step limit.

The four tools exposed to the model are:

- `connect_to_host` — opens the Boundary session and SSH shell (idempotent)
- `run_command` — executes a shell command and returns stdout and exit code
- `check_connection_status` — reports whether Boundary and SSH are active
- `disconnect_from_host` — cleanly closes everything

The system prompt gives the model strict rules: always connect before running commands, never disconnect unless explicitly asked, respond only in JSON. Granite follows these reliably.

---

## Keeping the Agent Safe: Prompt-Level Safety Rules

One of the first questions when giving an AI agent access to a live shell is: what stops it from running something destructive? For a non-production system, the answer is a dedicated set of safety rules embedded directly in the system prompt.

The agent's system prompt is divided into two rule sets. The first covers **operational behaviour** — connect before running commands, preserve the session, disconnect only when asked. The second, added as a `## Safety Rules` block, covers **what the agent must never do**:

- **No destructive commands.** `rm -rf`, `dd`, `mkfs`, `shutdown`, `reboot`, `poweroff`, `init 0`, `init 6` — commands that are irreversible or that take down the host — are explicitly forbidden.
- **No user or credential changes.** `useradd`, `userdel`, `passwd` are off-limits. The agent has no business modifying system accounts.
- **No firewall or network rule changes.** `iptables`, `ufw`, `nftables` — anything that could lock out access or change network policy — is excluded.
- **No raw device writes.** Writing directly to block devices (e.g. `> /dev/sda`) is prohibited regardless of context.
- **Explain and suggest alternatives.** If a user asks for something that would require a dangerous command, the agent explains why it cannot comply and offers a safe read-only alternative instead.
- **Default to caution.** When the agent is uncertain whether a command is safe, it does not run it — it asks the user to clarify first.

These rules live entirely in the system prompt rather than in code. That means the model reads them on every turn as part of the fixed context window — they are always active, not something that can be skipped. For a development or demo environment this is the right level of control: it prevents accidental misuse without adding code complexity. IBM Granite and Llama models follow these kinds of explicit negative constraints reliably.

The honest caveat: prompt-level rules are a soft guard. A carefully crafted adversarial input, or command output on the remote host that contains injected instructions, could in principle bypass them. For a production system, a code-level blocklist in `execute_tool` — checked before any command reaches the SSH layer — would be the appropriate enforcement point. For a personal demo on a dev host, the prompt rules are sufficient.

---

## The REPL: Startup Flow, Auth Selection, and Inactivity Timeout

The terminal REPL (`main.py`) grew beyond a simple input loop. Three pieces are worth calling out.

**Auth method selection.** Running `python main.py` prompts the user to choose `password` or `oidc` interactively before anything else happens. The choice can also be passed as a CLI flag (`--auth password` or `--auth oidc`) to skip the prompt entirely — useful for scripting or CI. The resolved method is written back into the environment so `boundary_session.py` picks it up without any extra plumbing.

**Masked password prompt.** When using password auth and `BOUNDARY_PASSWORD` is not already set in the environment, the REPL prompts for it using `getpass` — input is hidden in the terminal. This avoids the common mistake of typing a password into a visible input or embedding it in shell history. In CI or automated contexts where the env var is pre-set, the prompt is skipped entirely.

**Inactivity timeout.** Any running Boundary session consumes a license seat and leaves an auditable open session in the Boundary admin console. Leaving an agent running unattended with an open session is wasteful and a mild security concern. The REPL uses a `threading.Timer` that fires after `INACTIVITY_TIMEOUT_SECONDS` of silence (default: 3600 seconds / 1 hour) and automatically calls `ssh_exec.disconnect()` + `boundary_session.disconnect()`. The timer resets on every user turn. On an explicit disconnect or clean exit it is cancelled. The timeout can be overridden via the env var — set it to `300` for a 5-minute timeout during testing, or `0` to disable it is not supported (the default of 3600 applies to any non-numeric value).

---

## The Boundary Integration

Integrating Boundary turned out to be the most technically involved part — not because Boundary is complex, but because there were several version-specific behaviours to navigate with CLI v0.21.

Authentication was the first hurdle. The `boundary authenticate password` command in v0.21 refuses plain-string passwords as a security measure — they must be passed via `env://` or `file://` references. The fix was to inject the password into a temporary environment variable and reference it:

```python
env = {**os.environ, "_BOUNDARY_PASSWORD": os.environ["BOUNDARY_PASSWORD"]}
subprocess.run([
    "boundary", "authenticate", "password",
    f"-addr={os.environ['BOUNDARY_ADDR']}",
    f"-auth-method-id={os.environ['BOUNDARY_AUTH_METHOD_ID']}",
    f"-login-name={os.environ['BOUNDARY_LOGIN_NAME']}",
    "-password=env://_BOUNDARY_PASSWORD",
    "-format=json",
], env=env, ...)
```

The second discovery was about `boundary connect ssh -style=none`. The original plan was to use this to get a raw TCP proxy and connect paramiko to it. In v0.21, `-style=none` is not a valid style — valid options are `ssh` and `putty`. The correct command for a raw proxy is `boundary connect` without the `ssh` subcommand. This variant emits a single JSON line with the local proxy `address`, `port`, and `session_id`, then stays alive as the tunnel — exactly what was needed.

The JSON output from `boundary connect` also includes the injected credentials — the username and password that Boundary has stored in its credential store for the target. These are extracted and passed through to the SSH layer, completing the credential injection chain without the agent ever having static knowledge of the SSH password.

---

## The SSH Layer

Connecting paramiko to the Boundary proxy revealed another subtlety. The Boundary proxy presents an SSH interface but handles credential injection at the Worker layer — meaning it accepts `auth_none` from the connecting client. Standard `SSHClient.connect()` attempts password or key authentication by default and fails because no authentication is actually required from the client side.

The fix was to drop `SSHClient` entirely and use `paramiko.Transport` directly, calling `auth_none()` explicitly:

```python
t = paramiko.Transport(sock)
t.connect()
try:
    t.auth_none(username)
except paramiko.BadAuthenticationType as e:
    # Fall back to password if proxy requires it
    t.auth_password(username, password)
```

The initial implementation used `exec_command()` to run each command in its own channel — which works, but has a significant limitation: **each exec invocation is a separate, stateless session**. A `cd /tmp` followed by `pwd` would return the home directory, not `/tmp`. More importantly, Boundary's session recording would log dozens of discrete exec invocations rather than a single interactive session.

The solution was to replace the per-command exec model with a **persistent interactive shell**. The connection flow became: open a Transport, authenticate, request a PTY with `get_pty()`, invoke a shell with `invoke_shell()`, and keep that channel alive for the lifetime of the conversation. Commands are sent over stdin and output is read back until a shell prompt is detected.

To cleanly delimit each command's output, a unique sentinel string is appended after every command and echoed with the exit code embedded:

```python
sentinel = f"__X{uuid.uuid4().hex}__"
shell.sendall(f"{command} ; echo {sentinel}$?\n".encode())
```

The output parser reads until the prompt reappears, finds the sentinel line to extract the exit code, and strips everything else — prompt lines, ANSI escape sequences, terminal echo of the sent command — leaving clean output for the model to reason about.

---

## Debugging and What Was Learned

A handful of real-world debugging moments shaped the final design:

**WatsonX model availability.** The original model ID `ibm/granite-3-8b-instruct` was not available in the test WatsonX project. The supported instruct model was `meta-llama/llama-3-3-70b-instruct`. Switching also required moving from the deprecated `generate_text()` API to the SDK's native `model.chat()` method, which handles prompt formatting per-model automatically and avoids manually constructing Granite-specific chat templates.

**The PROMPT_COMMAND noise.** The Ubuntu instance had a `PROMPT_COMMAND` that printed AWS instance metadata (account ID, hostname, region, instance ID) before every prompt. This contaminated command output until `PROMPT_COMMAND=''` was set immediately after connecting — a one-line fix that keeps output clean for the model.

**Prompt-based output delimiting.** The remote shell's prompt gave a reliable terminator for reads. Combined with the sentinel for exit code extraction, the output parser became: skip the command echo, collect lines until the sentinel, stop. Clean and deterministic.

---

## A Word on Secrets and Risk

Before you run any of this, it is worth being direct about something: the `.env` file used in this project is for **local illustration only**. It is a convenient way to get the agent running quickly in a development environment, but it is not how you should handle credentials in any shared, team, or production context.

The risks of treating `.env` files carelessly are real. A file that contains an API key, a Boundary password, or an SSH username sitting on disk is one accidental `git push`, one screenshot, or one shared terminal session away from being exposed. Even if the `.gitignore` is correct, secrets in plaintext on a developer's machine can be harvested by malicious packages, synced inadvertently to cloud storage, or simply copied when a laptop is handed off. AI agents add a specific dimension to this risk: a compromised credential does not just expose a server — it exposes a reasoning engine that can be instructed to run arbitrary commands on that server.

**For anything beyond a personal demo, secrets must be managed externally.** The right tool for this in the HashiCorp ecosystem is **HashiCorp Vault**, which can store API keys, passwords, and certificates centrally, issue short-lived dynamic credentials, and enforce access policies on who and what can retrieve them. Rather than reading `BOUNDARY_PASSWORD` from a `.env` file, a production version of this agent would call the Vault API at startup to fetch the credential, use it for the session lifetime, and never write it to disk. HCP Vault Secrets — the managed cloud version — makes this straightforward to set up alongside HCP Boundary.

Beyond Vault, the broader principle is: **never commit secrets to source control, never log them, and rotate them regularly**. The `.env.example` in this repository contains placeholder values only. The real `.env` is git-ignored. If you are adapting this project, audit your own `.gitignore`, use your organisation's secrets manager (Vault, AWS Secrets Manager, Azure Key Vault, or equivalent), and consider using short-lived API keys with the minimum required permissions rather than long-lived master credentials.

HCP Boundary's credential injection model is itself a step in the right direction — the agent never holds the SSH password statically. Adding Vault for the remaining secrets (the WatsonX API key and the Boundary password used to authenticate the agent itself) closes the loop on a genuinely secrets-free application configuration.

---

## What You Need to Run This

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | [python.org/downloads](https://www.python.org/downloads/) |
| `boundary` CLI | 0.19+ | Must match your HCP Boundary cluster version — [install guide](https://developer.hashicorp.com/boundary/install) |
| HCP Boundary account | — | SSH target with credential injection configured |
| WatsonX.ai access | — | IBM Cloud account with a WatsonX project and API key |

### Python dependencies

Install everything with:

```bash
pip install -r requirements.txt
```

The full dependency list:

| Package | Purpose |
|---|---|
| `ibm-watsonx-ai >= 1.0.0` | WatsonX.ai SDK — model inference and chat API |
| `paramiko >= 3.4.0` | SSH client — persistent shell through the Boundary proxy |
| `python-dotenv >= 1.0.0` | Loads credentials from `.env` at runtime |
| `rich >= 13.7.0` | Terminal formatting for the chat REPL |
| `pytest >= 8.0.0` | Test runner |
| `pytest-mock >= 3.12.0` | Mocking support for offline unit tests |

### Configuration

Copy `.env.example` to `.env` and fill in your values:

```dotenv
# WatsonX.ai
WATSONX_API_KEY=<your-ibm-cloud-api-key>
WATSONX_PROJECT_ID=<your-watsonx-project-id>
WATSONX_URL=https://us-south.ml.cloud.ibm.com
WATSONX_MODEL_ID=meta-llama/llama-3-3-70b-instruct

# HCP Boundary — authentication
BOUNDARY_ADDR=https://<your-cluster-id>.boundary.hashicorp.cloud
BOUNDARY_AUTH_METHOD=password
BOUNDARY_AUTH_METHOD_ID=ampw_<your-auth-method-id>
BOUNDARY_LOGIN_NAME=<your-boundary-username>
BOUNDARY_PASSWORD=<your-boundary-password>

# HCP Boundary — SSH target
BOUNDARY_TARGET_ID=tssh_<your-target-id>
SSH_USERNAME=<linux-username>

# Agent behaviour (optional)
INACTIVITY_TIMEOUT_SECONDS=3600   # auto-disconnect after this many seconds of inactivity
```

---

## Testing

The test suite covers all three modules — WatsonX, Boundary session, and SSH execution — entirely offline. No live API calls are made in the test run.

```bash
pytest tests/ -v
# 24 passed in 0.35s
```

Running the agent end-to-end looks like this:

```
$ python main.py
Auth method [password/oidc] (default: password):
Boundary password: ****

─────────── WatsonX + HCP Boundary Agent ───────────
Auth: password · Inactivity timeout: 1h · Ask me anything about your Ubuntu host.
Type quit or exit to end the session.

[disconnected] You: Connect to the host and show me disk usage

Agent: Connected. Disk usage:
  Filesystem      Size  Used Avail Use%  Mounted on
  /dev/root       7.6G  3.2G  4.5G  42%  /

[connected ✓] You: Go to /tmp and list what's there

Agent: Changed to /tmp. Contents: [...]

[connected ✓] You: Check uptime

Agent:  22:14:18 up 99 days, 27 min, 1 user, load average: 0.04, 0.03, 0.01

[connected ✓] You: Disconnect

Agent: SSH connection closed. Boundary session terminated.
```

The Boundary admin console shows a single session for the entire conversation, with a continuous interactive recording — not a stream of isolated exec calls.

---

## Coming Next: OIDC Authentication with IBM Verify

The current implementation uses Boundary's username/password auth method, which is suitable for service accounts and automation. The natural next step is to replace this with **OIDC**, enabling browser-based SSO through an enterprise identity provider.

**IBM Verify** is the intended IdP here — a standalone identity platform with full OIDC support that acts as the authentication broker for Boundary. With IBM Verify in place, every time the agent connects it triggers a browser-based login through your organisation's IdP. MFA, session duration limits, and group-based authorisation are all enforced by Verify and Boundary — without any changes to the agent code itself.

The agent already has the OIDC code path built in. Enabling it requires three steps:

1. Create an OIDC application in your IBM Verify tenant (issuer URL, client ID, client secret)
2. Configure an OIDC auth method in HCP Boundary pointing at the Verify tenant
3. Switch `.env`:
   ```dotenv
   BOUNDARY_AUTH_METHOD=oidc
   BOUNDARY_OIDC_AUTH_METHOD_ID=amoidc_<your-oidc-method-id>
   ```

That integration — and a walkthrough of the IBM Verify tenant setup — will be covered in a follow-up post.

---

## Get Started: Sign Up for the Tools

Everything used in this project is available to try right now. Here is where to start:

---

### IBM WatsonX.ai
Build and deploy AI models using IBM's foundation model library, including IBM Granite.

**→ [Try WatsonX.ai free](https://www.ibm.com/watsonx)**

Create an IBM Cloud account, provision a WatsonX project, and generate an API key at [cloud.ibm.com/iam/apikeys](https://cloud.ibm.com/iam/apikeys). The free tier is sufficient to run this agent.

---

### HashiCorp HCP Boundary
Zero-trust infrastructure access with session recording, credential injection, and fine-grained target authorisation — fully managed.

**→ [Start with HCP Boundary free](https://portal.cloud.hashicorp.com/sign-up)**

The HCP free tier includes Boundary with enough capacity to run this project. You will need to configure an SSH target with a static credential store to enable credential injection.

---

### HashiCorp Boundary CLI
The command-line client that the agent uses to authenticate and open proxy tunnels. Install it to match your HCP Boundary cluster version.

**→ [Install the Boundary CLI](https://developer.hashicorp.com/boundary/install)**

Available for macOS (Homebrew), Linux, and Windows. Version must match your HCP cluster.

---

### IBM Verify *(for the OIDC follow-up)*
Enterprise identity platform with OIDC, MFA, and SSO — the IdP that will replace username/password auth in the next iteration of this project.

**→ [Start an IBM Verify free trial](https://www.ibm.com/verify)**

The 90-day trial gives you a full tenant with OIDC application support, no credit card required.

---

### paramiko
The Python SSH library used for the persistent shell connection. Install it as part of the project dependencies — no separate account needed.

**→ [paramiko on PyPI](https://pypi.org/project/paramiko/)**

---

## The Bigger Picture

What makes this project interesting is not any single component — it is the combination. WatsonX provides the reasoning. Boundary provides the access control and audit trail. The persistent shell model means the agent behaves like a human operator, maintaining context across a conversation rather than firing isolated commands into the void.

The result is an agent that can be handed a vague question — "is this host healthy?" — and will connect, run the appropriate commands, interpret the output, and give a plain-English answer, all through a secure, audited, zero-standing-access session. That is the direction infrastructure tooling is heading, and the pieces to build it are available today.

---

*The full source code is in the [`ai-boundary-agent`](.) repository. All 24 tests pass offline. Run `python main.py` to start a conversation.*
