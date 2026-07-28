"""
agent_autonomous.py
Goal-driven autonomous agent loop powered by IBM Granite via WatsonX.ai.

Unlike the conversational agent (agent.py) which waits for a human turn before
each reasoning step, this agent:

  1. Accepts a single high-level GOAL string at startup.
  2. Plans a sequence of sub-tasks to achieve the goal (plan phase).
  3. Executes each sub-task autonomously using the same ReAct tool loop.
  4. Evaluates whether the goal has been met after each sub-task completes.
  5. Terminates automatically when the goal is achieved or MAX_GOAL_STEPS is reached.

Tools (identical to conversational agent — shared modules, no duplication):
  - connect_to_host         Open Boundary session + SSH (idempotent)
  - run_command             Execute a shell command on the connected host
  - check_connection_status Report whether Boundary + SSH are active
  - disconnect_from_host    Close SSH + Boundary session

Entry point:  main_autonomous.py
"""

import json
import os
import re

from dotenv import load_dotenv

from . import boundary_session, ssh_exec, watsonx_llm

load_dotenv()

MAX_REACT_STEPS = 10    # max tool-call steps per sub-task
MAX_GOAL_STEPS  = 8     # max sub-tasks the planner may schedule

# ---------------------------------------------------------------------------
# Tool definitions  (same schema as conversational agent)
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
            "Only call this when all sub-tasks are complete and no further commands are needed."
        ),
        "parameters": {},
    },
]

_TOOLS_JSON = json.dumps(TOOLS, indent=2)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """You are an autonomous Linux infrastructure agent.
Given a high-level GOAL, break it down into a short ordered list of concrete sub-tasks.
Each sub-task must be a self-contained instruction that can be executed independently.

Rules:
- Output ONLY a JSON array of strings — no prose, no markdown fences.
- Maximum {max_steps} sub-tasks.
- The last sub-task should always disconnect from the host.
- Keep sub-tasks specific and actionable (e.g. "Check CPU usage with top -bn1" not "check performance").

Example output:
["Connect to the host", "Check disk usage on all mount points", "List the top 5 memory-consuming processes", "Disconnect from the host"]
""".format(max_steps=MAX_GOAL_STEPS)

EXECUTOR_SYSTEM_PROMPT = f"""You are an autonomous Linux infrastructure agent executing a specific sub-task.
You have access to a remote Ubuntu host via HCP Boundary.

## Rules
1. Before running any command, ensure you are connected. Call connect_to_host first.
2. You may call run_command as many times as needed — the session stays open between calls.
3. Only call disconnect_from_host when explicitly told this is the final sub-task.
4. After completing the sub-task, give a concise result summary.

## Safety Rules
5. NEVER run destructive or irreversible commands: rm -rf, dd, mkfs, shutdown, reboot, poweroff, init 0/6.
6. NEVER modify system users or credentials: useradd, userdel, passwd.
7. NEVER make changes to firewall or network rules: iptables, ufw, nftables.
8. NEVER write to raw block devices (e.g. > /dev/sda).
9. If a sub-task would require a dangerous command, explain why you cannot do it and stop.

## Available Tools
{_TOOLS_JSON}

## Response Format
Respond with ONLY a single JSON object — no prose, no markdown fences.

To call a tool:
{{"action": "tool_name", "args": {{"key": "value"}}}}

For tools with no parameters:
{{"action": "connect_to_host", "args": {{}}}}

To complete the sub-task with a result:
{{"action": "final_answer", "answer": "Concise result of this sub-task."}}
"""

EVALUATOR_SYSTEM_PROMPT = """You are an autonomous agent evaluator.
Given the original GOAL and a list of completed sub-task results, determine whether the goal has been fully achieved.

Respond with ONLY a JSON object:
{{"goal_achieved": true, "reason": "Brief explanation."}}
or
{{"goal_achieved": false, "reason": "What is still missing."}}
"""

# ---------------------------------------------------------------------------
# Tool execution  (identical to conversational agent — no duplication)
# ---------------------------------------------------------------------------

def execute_tool(tool_name: str, args: dict) -> str:
    """Execute a named tool and return an observation string."""

    if tool_name == "connect_to_host":
        target_id = os.environ["BOUNDARY_TARGET_ID"]
        session = boundary_session.connect(target_id)
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
# LLM output parsing  (identical to conversational agent)
# ---------------------------------------------------------------------------

def _parse_action(text: str) -> dict | None:
    """Extract a JSON action object from LLM output. Handles markdown fences."""
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
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


def _parse_json_array(text: str) -> list[str] | None:
    """Extract a JSON array of strings from LLM output."""
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
        if isinstance(data, list) and all(isinstance(i, str) for i in data):
            return data
    except json.JSONDecodeError:
        pass
    return None


def _parse_evaluation(text: str) -> dict | None:
    """Extract a goal-achieved evaluation JSON from LLM output."""
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
        if "goal_achieved" in data:
            return data
    except json.JSONDecodeError:
        pass
    return None


# ---------------------------------------------------------------------------
# Phase 1: Planner
# ---------------------------------------------------------------------------

def plan(goal: str) -> list[str]:
    """
    Ask the LLM to decompose the GOAL into an ordered list of sub-tasks.

    Returns a list of sub-task strings. Falls back to a single-step plan
    if the model output cannot be parsed.
    """
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": f"GOAL: {goal}"},
    ]
    response = watsonx_llm.chat(messages)
    sub_tasks = _parse_json_array(response)
    if sub_tasks:
        return sub_tasks
    # Fallback: treat the whole goal as one sub-task
    return [goal, "Disconnect from the host"]


# ---------------------------------------------------------------------------
# Phase 2: Executor (per sub-task ReAct loop)
# ---------------------------------------------------------------------------

def execute_subtask(sub_task: str, is_final: bool, context: list[str]) -> str:
    """
    Run a single sub-task through the ReAct loop and return its result summary.

    Args:
        sub_task:  The sub-task instruction string from the planner.
        is_final:  True if this is the last sub-task (agent may disconnect).
        context:   Results of all previously completed sub-tasks (for continuity).

    Returns:
        A concise result string summarising what was done/found.
    """
    context_block = ""
    if context:
        context_block = "\n\nPrevious sub-task results:\n" + "\n".join(
            f"- {r}" for r in context
        )

    final_note = " This is the final sub-task — disconnect from the host when done." if is_final else ""

    user_content = f"Sub-task: {sub_task}{final_note}{context_block}"

    messages = [
        {"role": "system", "content": EXECUTOR_SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    for step in range(MAX_REACT_STEPS):
        llm_response = watsonx_llm.chat(messages)
        parsed = _parse_action(llm_response)

        if parsed is None:
            # Prose response — treat as the result
            return llm_response

        action = parsed.get("action", "")
        args   = parsed.get("args", {})

        if action == "final_answer":
            return parsed.get("answer", llm_response)

        # Tool call
        observation = execute_tool(action, args)
        messages.append({"role": "assistant", "content": llm_response})
        messages.append({"role": "user",      "content": f"Observation (step {step + 1}, tool={action}):\n{observation}"})

    return "Sub-task did not complete within the allowed number of steps."


# ---------------------------------------------------------------------------
# Phase 3: Evaluator
# ---------------------------------------------------------------------------

def evaluate(goal: str, results: list[str]) -> tuple[bool, str]:
    """
    Ask the LLM whether the original GOAL has been fully achieved.

    Returns:
        (goal_achieved: bool, reason: str)
    """
    results_block = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(results))
    messages = [
        {"role": "system", "content": EVALUATOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"GOAL: {goal}\n\n"
                f"Completed sub-task results:\n{results_block}"
            ),
        },
    ]
    response = watsonx_llm.chat(messages)
    evaluation = _parse_evaluation(response)
    if evaluation:
        return bool(evaluation["goal_achieved"]), evaluation.get("reason", "")
    # Default: assume achieved if we got results
    return True, "Evaluation inconclusive — assuming goal achieved."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(goal: str, on_plan: callable = None, on_subtask: callable = None) -> dict:
    """
    Run the full autonomous agent loop for a given goal.

    Args:
        goal:        High-level objective string (e.g. "Audit disk, memory, and running services").
        on_plan:     Optional callback(sub_tasks: list[str]) called after planning.
        on_subtask:  Optional callback(index: int, sub_task: str, result: str) called after each sub-task.

    Returns:
        {
          "goal":          str,
          "sub_tasks":     list[str],
          "results":       list[str],      # per-sub-task result summaries
          "goal_achieved": bool,
          "reason":        str,
        }
    """
    # Phase 1 — Plan
    sub_tasks = plan(goal)
    if on_plan:
        on_plan(sub_tasks)

    # Phase 2 — Execute
    results: list[str] = []
    for i, sub_task in enumerate(sub_tasks):
        is_final = (i == len(sub_tasks) - 1)
        result = execute_subtask(sub_task, is_final=is_final, context=results)
        results.append(result)
        if on_subtask:
            on_subtask(i, sub_task, result)

    # Phase 3 — Evaluate
    goal_achieved, reason = evaluate(goal, results)

    return {
        "goal":          goal,
        "sub_tasks":     sub_tasks,
        "results":       results,
        "goal_achieved": goal_achieved,
        "reason":        reason,
    }
