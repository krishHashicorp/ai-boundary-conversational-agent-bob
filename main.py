"""
main.py
Terminal chat REPL for the WatsonX + HCP Boundary AI agent.

Usage:
    python main.py              # prompts for auth method at startup
    python main.py --auth oidc  # skip prompt, use OIDC
    python main.py --auth password  # skip prompt, use password

Type your request in natural language. The agent will connect to the Ubuntu
host via HCP Boundary, run the necessary commands, and report back.
Type 'quit' or 'exit' (or press Ctrl+C) to end the session.
"""

import argparse
import atexit
import getpass
import os
import readline  # noqa: F401  — importing activates arrow-key / history support for input()
import sys
import threading

from dotenv import load_dotenv
from rich.console import Console
from rich.rule import Rule

load_dotenv()

from src.agent import agent, boundary_session, ssh_exec  # noqa: E402

console = Console()


# ---------------------------------------------------------------------------
# Inactivity timeout
# ---------------------------------------------------------------------------

_inactivity_timer: threading.Timer | None = None


def _get_inactivity_timeout() -> float:
    """Read INACTIVITY_TIMEOUT_SECONDS from env (default: 3600 = 1 hour)."""
    try:
        return float(os.environ.get("INACTIVITY_TIMEOUT_SECONDS", "3600"))
    except ValueError:
        return 3600.0


def _on_inactivity_timeout() -> None:
    """Called by the timer thread when inactivity timeout fires."""
    if boundary_session.is_connected() or ssh_exec.is_connected():
        console.print(
            "\n[bold yellow]⏰ Inactivity timeout reached — disconnecting from host and Boundary.[/bold yellow]"
        )
        ssh_exec.disconnect()
        boundary_session.disconnect()


def _reset_inactivity_timer() -> None:
    """Cancel the current timer and start a fresh one."""
    global _inactivity_timer
    if _inactivity_timer is not None:
        _inactivity_timer.cancel()
    timeout = _get_inactivity_timeout()
    _inactivity_timer = threading.Timer(timeout, _on_inactivity_timeout)
    _inactivity_timer.daemon = True   # won't block Python from exiting
    _inactivity_timer.start()


def _cancel_inactivity_timer() -> None:
    """Stop the timer (called on clean exit)."""
    global _inactivity_timer
    if _inactivity_timer is not None:
        _inactivity_timer.cancel()
        _inactivity_timer = None


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _cleanup() -> None:
    """Ensure Boundary and SSH are disconnected on exit."""
    _cancel_inactivity_timer()
    ssh_exec.disconnect()
    boundary_session.disconnect()


atexit.register(_cleanup)


def _resolve_auth_method(flag: str | None) -> str:
    """
    Determine the Boundary auth method to use.

    Priority:
      1. --auth CLI flag (if provided)
      2. Interactive prompt (only when running in a TTY)
      3. BOUNDARY_AUTH_METHOD env var from .env (fallback / default)
    """
    if flag:
        method = flag.lower()
        if method not in {"password", "oidc"}:
            console.print(f"[bold red]Unknown auth method '{flag}'. Choose 'password' or 'oidc'.[/bold red]")
            sys.exit(1)
        return method

    # Interactive prompt — only when stdin is a real terminal
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
        method = choice if choice in {"password", "oidc"} else env_default
        return method

    # Non-interactive (piped input, CI, etc.) — use .env value
    return os.environ.get("BOUNDARY_AUTH_METHOD", "password").lower()


def _prompt_password_if_needed(auth_method: str) -> None:
    """
    When using password auth, prompt the user for their Boundary password
    (input is hidden) and inject it into the environment.

    Skipped when:
    - auth method is not 'password'
    - BOUNDARY_PASSWORD is already set in the environment (e.g. for CI)
    - stdin is not a TTY (non-interactive; falls back to env var)
    """
    if auth_method != "password":
        return

    # If already supplied via env (CI / scripted use), don't prompt
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


def main() -> None:
    parser = argparse.ArgumentParser(description="WatsonX + HCP Boundary AI Agent")
    parser.add_argument(
        "--auth",
        metavar="METHOD",
        help="Boundary auth method: 'password' or 'oidc' (overrides .env and startup prompt)",
    )
    args = parser.parse_args()

    # Resolve auth method and inject into the environment so boundary_session picks it up
    auth_method = _resolve_auth_method(args.auth)
    os.environ["BOUNDARY_AUTH_METHOD"] = auth_method

    # Prompt for password (masked) if using password auth and not already in env
    _prompt_password_if_needed(auth_method)

    timeout_secs = _get_inactivity_timeout()
    timeout_display = (
        f"{int(timeout_secs // 3600)}h" if timeout_secs >= 3600
        else f"{int(timeout_secs // 60)}m" if timeout_secs >= 60
        else f"{int(timeout_secs)}s"
    )

    console.print(Rule("[bold]WatsonX + HCP Boundary Agent[/bold]"))
    console.print(
        f"[dim]Auth: [bold]{auth_method}[/bold] · "
        f"Inactivity timeout: [bold]{timeout_display}[/bold] · "
        "Ask me anything about your Ubuntu host.[/dim]"
    )
    console.print("[dim]Type [bold]quit[/bold] or [bold]exit[/bold] to end the session.[/dim]\n")

    history: list[dict] = []
    _reset_inactivity_timer()   # start the timer from launch

    while True:
        # Show connection status in the prompt
        if boundary_session.is_connected():
            status = "[bold green]\\[connected ✓][/bold green]"
        else:
            status = "[dim]\\[disconnected][/dim]"

        try:
            console.print(f"{status} [bold blue]You:[/bold blue] ", end="", highlight=False)
            user_input = input().strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue

        # Any input resets the inactivity timer
        _reset_inactivity_timer()

        if user_input.lower() in {"quit", "exit"}:
            console.print("[dim]Closing session and disconnecting...[/dim]")
            break

        try:
            response, history = agent.run(user_input, history)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[bold red]Error:[/bold red] {exc}")
            continue

        console.print(f"[bold green]Agent:[/bold green] {response}\n")

    _cleanup()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        sys.exit(0)
