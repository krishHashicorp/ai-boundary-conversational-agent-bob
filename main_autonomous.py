"""
main_autonomous.py
Autonomous entry point for the WatsonX + HCP Boundary AI Agent.

Unlike the conversational REPL (main.py), this entry point accepts a single
high-level goal and runs the agent autonomously through three phases:

  Plan    → decompose the goal into ordered sub-tasks
  Execute → run each sub-task via the ReAct tool loop
  Evaluate → confirm the goal was fully achieved

Usage:
    python main_autonomous.py "Audit disk usage, memory, and top CPU processes, then report"
    python main_autonomous.py --auth oidc "Check if nginx is running and report its status"
    python main_autonomous.py --auth password "List all cron jobs for the current user"

The conversational agent (main.py) is unchanged and fully preserved.
"""

import argparse
import atexit
import getpass
import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

load_dotenv()

from src.agent import agent_autonomous, boundary_session, ssh_exec  # noqa: E402

console = Console()


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _cleanup() -> None:
    """Ensure Boundary and SSH are disconnected on exit."""
    ssh_exec.disconnect()
    boundary_session.disconnect()


atexit.register(_cleanup)


# ---------------------------------------------------------------------------
# Auth helpers  (identical logic to main.py — same .env, same methods)
# ---------------------------------------------------------------------------

def _resolve_auth_method(flag: str | None) -> str:
    if flag:
        method = flag.lower()
        if method not in {"password", "oidc"}:
            console.print(f"[bold red]Unknown auth method '{flag}'. Choose 'password' or 'oidc'.[/bold red]")
            sys.exit(1)
        return method
    if sys.stdin.isatty():
        env_default = os.environ.get("BOUNDARY_AUTH_METHOD", "password").lower()
        console.print(
            f"[bold]Auth method[/bold] [dim]\\[password/oidc][/dim] "
            f"[dim](default: {env_default}):[/dim] ",
            end="",
            highlight=False,
        )
        try:
            choice = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Aborted.[/dim]")
            sys.exit(0)
        return choice if choice in {"password", "oidc"} else env_default
    return os.environ.get("BOUNDARY_AUTH_METHOD", "password").lower()


def _prompt_password_if_needed(auth_method: str) -> None:
    if auth_method != "password":
        return
    if os.environ.get("BOUNDARY_PASSWORD"):
        return
    if not sys.stdin.isatty():
        return
    console.print("[bold]Boundary password:[/bold] ", end="", highlight=False)
    try:
        password = getpass.getpass(prompt="")
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Aborted.[/dim]")
        sys.exit(0)
    if not password:
        console.print("[bold red]Password cannot be empty.[/bold red]")
        sys.exit(1)
    os.environ["BOUNDARY_PASSWORD"] = password


# ---------------------------------------------------------------------------
# Rich progress callbacks
# ---------------------------------------------------------------------------

def _on_plan(sub_tasks: list[str]) -> None:
    """Print the planned sub-tasks before execution begins."""
    console.print()
    console.print(Rule("[bold]Plan[/bold]"))
    for i, task in enumerate(sub_tasks, 1):
        console.print(f"  [dim]{i}.[/dim] {task}")
    console.print()


def _on_subtask(index: int, sub_task: str, result: str) -> None:
    """Print each sub-task result as it completes."""
    console.print(Rule(f"[bold]Step {index + 1}[/bold] — {sub_task}", style="dim"))
    console.print(f"[green]{result}[/green]")
    console.print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="WatsonX + HCP Boundary Autonomous Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python main_autonomous.py "Audit disk, memory and top CPU processes"\n'
            '  python main_autonomous.py --auth oidc "Check if nginx is running"\n'
        ),
    )
    parser.add_argument(
        "goal",
        metavar="GOAL",
        help="High-level objective for the agent to achieve autonomously",
    )
    parser.add_argument(
        "--auth",
        metavar="METHOD",
        help="Boundary auth method: 'password' or 'oidc' (overrides .env and startup prompt)",
    )
    args = parser.parse_args()

    auth_method = _resolve_auth_method(args.auth)
    os.environ["BOUNDARY_AUTH_METHOD"] = auth_method
    _prompt_password_if_needed(auth_method)

    console.print(Rule("[bold]WatsonX + HCP Boundary Autonomous Agent[/bold]"))
    console.print(f"[dim]Auth: [bold]{auth_method}[/bold] · Mode: [bold]autonomous[/bold][/dim]")
    console.print(f"\n[bold]Goal:[/bold] {args.goal}\n")

    try:
        outcome = agent_autonomous.run(
            goal=args.goal,
            on_plan=_on_plan,
            on_subtask=_on_subtask,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)

    # Final summary table
    console.print(Rule("[bold]Summary[/bold]"))
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("Sub-task", style="dim", width=40)
    table.add_column("Result")
    for task, result in zip(outcome["sub_tasks"], outcome["results"]):
        table.add_row(task, result)
    console.print(table)

    console.print()
    if outcome["goal_achieved"]:
        console.print(f"[bold green]✓ Goal achieved:[/bold green] {outcome['reason']}")
    else:
        console.print(f"[bold yellow]⚠ Goal not fully achieved:[/bold yellow] {outcome['reason']}")

    _cleanup()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        sys.exit(0)
