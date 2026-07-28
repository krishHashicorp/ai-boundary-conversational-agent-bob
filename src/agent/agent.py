"""
agent.py
ReAct (Reason + Act) agent loop powered by IBM Granite via WatsonX.ai.

Tools available to the agent:
  - connect_to_host         Open Boundary session + SSH (idempotent)
  - run_command             Execute a shell command on the connected host
  - check_connection_status Report whether Boundary + SSH are active
  - disconnect_from_host    Close SSH + Boundary session

Session model: connect once, issue many run_command calls, disconnect only
when the user explicitly requests it or says goodbye.
"""

import json
import os
import re

from dotenv import load_dotenv

from . import boundary_session, ssh_exec, watsonx_llm

load_dotenv()

MAX_STEPS = 10

# ---------------------------------------------------------------------------
# Tool definitions (JSON Schema style, embedded in the system prompt)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "connect_to_host",
        "description": (
            "Open an HCP Boundary session and SSH connection to the Ubuntu target. "
            "Safe to call even if already connected (no-op). "
            "Call this before any run_command."
        ),
        "parameters": {},
    },
    {
        "name": "run_command",
        "description": (
            "Execute a shell command on the connected Ubuntu host and return stdout, "
            "stderr, and exit code. Requires connect_to_host to have been called first. "
            "You may call this multiple times without reconnecting."
        ),
        "parameters": {
            "command": {
                "type": "string",
                "description": "The shell command to execute on the remote host.",
            }
        },
    },
    {
        "name": "check_connection_status",
        "description": "Check whether the Boundary proxy and SSH connection are currently active.",
        "parameters": {},
    },
    {
        "name": "disconnect_from_host",
        "description": (
            "Close the SSH connection and Boundary session. "
            "Only call this when the user explicitly asks to disconnect, "
            "says they are done, or says goodbye."
        ),
        "parameters": {},
    },
]

_TOOLS_JSON = json.dumps(TOOLS, indent=2)

AGENT_SYSTEM_PROMPT = f"""You are a Linux infrastructure assistant with access to a remote Ubuntu host via HCP Boundary.

## Rules
1. Before running any command, ensure you are connected. Call connect_to_host first.
2. You may call run_command as many times as needed — the session stays open between calls.
3. Only call disconnect_from_host when the user explicitly asks to disconnect, says they are finished, or says goodbye.
4. Use check_connection_status if you are unsure whether a session is already active.
5. After gathering all necessary information, give a clear, concise final answer.

## Safety Rules
6. NEVER run commands that are destructive or irreversible: rm -rf, dd, mkfs, shutdown, reboot, poweroff, init 0, init 6.
7. NEVER modify system users or credentials: useradd, userdel, passwd.
8. NEVER make changes to firewall or network rules: iptables, ufw, nftables.
9. NEVER write to raw block devices (e.g. > /dev/sda).
10. If the user asks for something that would require a dangerous command, explain why you cannot do it and suggest a safe read-only alternative instead.
11. When uncertain whether a command is safe, do NOT run it — ask the user to clarify first.

## Available Tools
{_TOOLS_JSON}

## Response Format
You must respond with ONLY a single JSON object — no prose, no markdown fences.

To call a tool:
{{"action": "tool_name", "args": {{"key": "value"}}}}

For tools with no parameters use an empty args object:
{{"action": "connect_to_host", "args": {{}}}}

To give a final answer to the user:
{{"action": "final_answer", "answer": "Your response here."}}
"""


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def execute_tool(tool_name: str, args: dict) -> str:
    """Execute a named tool with the given arguments and return an observation string."""

    if tool_name == "connect_to_host":
        target_id = os.environ["BOUNDARY_TARGET_ID"]
        session = boundary_session.connect(target_id)
        # Use injected credentials from Boundary if available, fall back to env var username
        username = session.get("injected_username") or os.environ["SSH_USERNAME"]
        password = session.get("injected_password")
        ssh_exec.connect(
            host=session["host"],
            port=session["port"],
            username=username,
            password=password,
        )
        return (
            f"Connected. Boundary session active (session_id={session['session_id']}). "
            f"SSH connected to {session['host']}:{session['port']} as {username}."
        )

    if tool_name == "run_command":
        command = args.get("command", "")
        result = ssh_exec.run_command(command)
        parts = [f"exit_code={result['exit_code']}"]
        if result["stdout"]:
            parts.append(f"stdout:\n{result['stdout']}")
        if result["stderr"]:
            parts.append(f"stderr:\n{result['stderr']}")
        return "\n".join(parts)

    if tool_name == "check_connection_status":
        boundary_ok = boundary_session.is_connected()
        ssh_ok = ssh_exec.is_connected()
        return (
            f"Boundary proxy: {'active' if boundary_ok else 'not connected'}. "
            f"SSH: {'active' if ssh_ok else 'not connected'}."
        )

    if tool_name == "disconnect_from_host":
        ssh_exec.disconnect()
        boundary_session.disconnect()
        return "SSH connection closed. Boundary session terminated."

    return f"Unknown tool: {tool_name}"


# ---------------------------------------------------------------------------
# LLM output parsing
# ---------------------------------------------------------------------------

def parse_llm_output(text: str) -> dict | None:
    """
    Extract a JSON action object from the LLM response.

    Handles cases where the model wraps JSON in markdown code fences.
    Returns the parsed dict or None if no valid action JSON is found.
    """
    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?\s*", "", text).strip()

    # Find the first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group())
        if "action" in data:
            return data
    except json.JSONDecodeError:
        pass

    return None


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

def run(user_message: str, history: list[dict]) -> tuple[str, list[dict]]:
    """
    Process a user message through the ReAct loop and return the final answer.

    Args:
        user_message: The latest message from the user
        history:      Conversation history (list of role/content dicts)

    Returns:
        (answer, updated_history)
    """
    # Build the working message list: system prompt + history + new user message
    messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    updated_history = list(history) + [{"role": "user", "content": user_message}]

    for step in range(MAX_STEPS):
        llm_response = watsonx_llm.chat(messages)
        parsed = parse_llm_output(llm_response)

        if parsed is None:
            # Model returned prose instead of JSON — treat as final answer
            updated_history.append({"role": "assistant", "content": llm_response})
            return llm_response, updated_history

        action = parsed.get("action", "")
        args = parsed.get("args", {})

        if action == "final_answer":
            answer = parsed.get("answer", llm_response)
            updated_history.append({"role": "assistant", "content": answer})
            return answer, updated_history

        # Tool call — execute and feed the observation back
        observation = execute_tool(action, args)
        observation_msg = f"Observation (step {step + 1}, tool={action}):\n{observation}"

        # Append the assistant's tool-call decision and the observation to the working context
        messages.append({"role": "assistant", "content": llm_response})
        messages.append({"role": "user", "content": observation_msg})

    # Max steps reached
    fallback = "I was unable to complete the task within the allowed number of steps. Please try rephrasing your request."
    updated_history.append({"role": "assistant", "content": fallback})
    return fallback, updated_history
