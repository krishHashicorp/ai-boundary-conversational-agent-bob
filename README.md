# WatsonX + HCP Boundary AI Agent

A conversational AI agent that connects to a remote Ubuntu host through **HCP Boundary** and executes shell commands on your behalf — powered by **IBM Granite** via WatsonX.ai.

---

## Architecture

```
main.py (chat REPL)
  └─▶ agent.py (ReAct loop — IBM Granite via WatsonX.ai)
        ├─▶ connect_to_host       → boundary_session.py + ssh_exec.py  [opens once]
        ├─▶ run_command           → ssh_exec.run_command()              [reuses conn ×N]
        ├─▶ check_connection_status
        └─▶ disconnect_from_host → ssh_exec + boundary_session          [explicit only]
```

**Session model:** The agent connects once per chat session. All `run_command` calls reuse the same persistent SSH connection through the Boundary proxy tunnel — no reconnect between commands. The session is only torn down when you explicitly ask the agent to disconnect, say you're done, or exit the chat.

**Credential injection:** The Ubuntu SSH password is stored in the HCP Boundary credential store and bound to the SSH target. The agent never handles the SSH password — Boundary injects it at the proxy layer. `paramiko` connects to the local proxy port with no credentials.

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

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```dotenv
# WatsonX.ai
WATSONX_API_KEY=<your-ibm-cloud-api-key>
WATSONX_PROJECT_ID=<your-watsonx-project-id>
WATSONX_URL=https://us-south.ml.cloud.ibm.com
WATSONX_MODEL_ID=ibm/granite-3-8b-instruct

# HCP Boundary — user authentication
BOUNDARY_ADDR=https://<cluster-id>.boundary.hashicorp.cloud
BOUNDARY_AUTH_METHOD=password
BOUNDARY_AUTH_METHOD_ID=ampw_<your-password-auth-method-id>
BOUNDARY_LOGIN_NAME=<your-boundary-username>
BOUNDARY_PASSWORD=<your-boundary-password>

# HCP Boundary — SSH target (tssh_ prefix for SSH target types)
BOUNDARY_TARGET_ID=tssh_<your-target-id>

# SSH username on the Ubuntu host
SSH_USERNAME=ubuntu
```

### 4. Verify connectivity

```bash
# Test Boundary authentication
boundary authenticate password \
  -auth-method-id=$BOUNDARY_AUTH_METHOD_ID \
  -login-name=$BOUNDARY_LOGIN_NAME \
  -password=$BOUNDARY_PASSWORD \
  -format=json

# Test Boundary SSH target
boundary connect ssh -target-id=$BOUNDARY_TARGET_ID -style=none -format=json
```

---

## Running the Agent

```bash
python main.py
```

The terminal shows a `[connected ✓]` / `[disconnected]` indicator in the prompt.

### Example conversation

```
[disconnected] You: Connect to the host and show me disk usage
Agent: Connected to the host. Disk usage:
  Filesystem      Size  Used Avail Use% Mounted on
  /dev/sda1        50G   12G   36G  25% /

[connected ✓] You: Now check available memory
Agent: Available memory: 3.2 GB free out of 8 GB total.

[connected ✓] You: What are the top 5 CPU-consuming processes?
Agent: The top 5 processes by CPU usage are:
  1. python3  (12.4%)
  2. nginx    (3.1%)
  ...

[connected ✓] You: Disconnect and summarise what you found
Agent: Disk is healthy (25% used), memory is comfortable (3.2 GB free),
and the host is lightly loaded. Disconnected.
```

---

## Switching to OIDC Authentication

OIDC replaces your Boundary username/password with browser-based SSO (Okta, Azure AD, Google, etc.).

### HCP Boundary setup (one-time)

1. In the HCP Boundary admin UI, go to **Auth Methods → New → OIDC**
2. Configure your IdP (issuer URL, client ID, client secret)
3. Note the generated **auth method ID** (starts with `amoidc_`)

### Agent configuration

Add the OIDC auth method ID to `.env` and switch the method:

```dotenv
BOUNDARY_AUTH_METHOD=oidc
BOUNDARY_OIDC_AUTH_METHOD_ID=amoidc_<your-oidc-method-id>
```

When you next run `python main.py` and ask the agent to connect, a browser window will open for you to log in. The token is cached automatically.

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
│       ├── watsonx_llm.py       # WatsonX.ai IBM Granite wrapper
│       ├── boundary_session.py  # HCP Boundary session lifecycle
│       ├── ssh_exec.py          # Persistent paramiko SSH connection
│       └── agent.py             # ReAct agent loop + tool definitions
├── tests/
│   ├── test_watsonx_llm.py
│   ├── test_boundary_session.py
│   └── test_ssh_exec.py
├── main.py                      # Terminal chat entry point
├── .env.example
├── requirements.txt
└── README.md
```
