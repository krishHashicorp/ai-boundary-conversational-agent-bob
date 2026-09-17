"""
boundary_session.py
Manages the HCP Boundary session lifecycle for an SSH target with credential injection.

Session model:
- authenticate() once to obtain a Boundary token
- connect() opens `boundary connect` as a long-lived background process;
  returns the local proxy host/port that paramiko should connect to
- The process stays alive for the entire chat session (no teardown between commands)
- disconnect() terminates the process cleanly
- connect() is idempotent: calling it when already connected returns existing info
"""

import atexit
import getpass
import json
import os
import subprocess
import sys
import threading
from dotenv import load_dotenv


class BoundarySessionCancelledError(RuntimeError):
    """Raised when the Boundary session is cancelled externally (e.g. by an admin)."""

load_dotenv()

# Module-level session state
_session_proc: subprocess.Popen | None = None
_session_info: dict | None = None  # {"host": "127.0.0.1", "port": int, "session_id": str}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

_MAX_PASSWORD_ATTEMPTS = 3


def authenticate_password() -> None:
    """
    Authenticate to HCP Boundary using username/password credentials.
    Sets the BOUNDARY_TOKEN environment variable for subsequent CLI calls.

    boundary CLI v0.19+ requires passwords via env:// or file:// — plain
    string values are rejected. We pass the password via a temporary env var.

    If the password is wrong (non-zero exit from boundary CLI), the user is
    prompted to re-enter it (masked). Up to _MAX_PASSWORD_ATTEMPTS tries total.
    """
    is_tty = sys.stdin.isatty()

    for attempt in range(1, _MAX_PASSWORD_ATTEMPTS + 1):
        password = os.environ.get("BOUNDARY_PASSWORD", "")

        # If no password in env and we're in a TTY, prompt now
        if not password and is_tty:
            try:
                password = getpass.getpass("Boundary password: ")
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                sys.exit(0)
            if not password:
                print("Password cannot be empty.")
                continue
            os.environ["BOUNDARY_PASSWORD"] = password

        env = {**os.environ, "_BOUNDARY_PASSWORD": password}
        result = subprocess.run(
            [
                "boundary", "authenticate", "password",
                f"-addr={os.environ['BOUNDARY_ADDR']}",
                f"-auth-method-id={os.environ['BOUNDARY_AUTH_METHOD_ID']}",
                f"-login-name={os.environ['BOUNDARY_LOGIN_NAME']}",
                "-password=env://_BOUNDARY_PASSWORD",
                "-format=json",
            ],
            capture_output=True,
            text=True,
            env=env,
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            token = data["item"]["attributes"]["token"]
            os.environ["BOUNDARY_TOKEN"] = token
            return

        # Authentication failed — clear the cached password and retry if TTY
        os.environ.pop("BOUNDARY_PASSWORD", None)
        remaining = _MAX_PASSWORD_ATTEMPTS - attempt
        if is_tty and remaining > 0:
            print(f"Authentication failed. Please try again ({remaining} attempt(s) left).")
        else:
            raise RuntimeError(
                f"Boundary password authentication failed after {attempt} attempt(s)."
            )


def authenticate_oidc() -> None:
    """
    Authenticate to HCP Boundary using OIDC (browser-based SSO).
    Blocks until the human completes the browser flow (IBM Verify + MFA).
    Sets the BOUNDARY_TOKEN environment variable.

    Boundary CLI v0.19–0.20: token written to -output-file (temp file).
    Boundary CLI v0.21+:     -output-file removed; token JSON is written to
                             stdout with -format=json; the browser callback
                             URL and progress messages go to stderr so the
                             human sees them in the terminal.

    We capture stdout to extract the token. stderr is left as-is (inherited)
    so the browser URL and login progress remain visible to the operator.

    -keyring-type=none   — skip OS keychain; we own the token lifetime
    -token-name=none     — don't persist a named token entry in the keyring
    """
    result = subprocess.run(
        [
            "boundary", "authenticate", "oidc",
            f"-addr={os.environ['BOUNDARY_ADDR']}",
            f"-auth-method-id={os.environ['BOUNDARY_OIDC_AUTH_METHOD_ID']}",
            "-keyring-type=none",
            "-token-name=none",
            "-format=json",
        ],
        stdout=subprocess.PIPE,
        # stderr intentionally not captured — browser URL must stay visible
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    token = data["item"]["attributes"]["token"]
    os.environ["BOUNDARY_TOKEN"] = token


def authenticate() -> None:
    """
    Authenticate to HCP Boundary using the method configured by BOUNDARY_AUTH_METHOD
    env var (defaults to 'password'). Dispatches to authenticate_password() or
    authenticate_oidc().

    Skipped entirely if BOUNDARY_TOKEN is already set in the environment — this
    allows a human to pre-authenticate via OIDC (browser + MFA) once and then
    hand off to the agent without triggering a second browser flow.
    """
    if os.environ.get("BOUNDARY_TOKEN"):
        return

    method = os.environ.get("BOUNDARY_AUTH_METHOD", "password").lower()
    if method == "oidc":
        authenticate_oidc()
    else:
        authenticate_password()


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def is_connected() -> bool:
    """Return True if the Boundary proxy process is currently running."""
    return _session_proc is not None and _session_proc.poll() is None


def check_cancelled() -> None:
    """
    Raise BoundarySessionCancelledError if the Boundary proxy process has exited
    unexpectedly — i.e. the session was cancelled from outside (e.g. by an admin
    terminating the session in the Boundary control plane).

    A non-zero return code indicates an external cancellation rather than a clean
    disconnect initiated by this process (which sets _session_proc to None first).
    """
    if _session_proc is not None and _session_proc.poll() is not None:
        raise BoundarySessionCancelledError(
            "Boundary session has been cancelled externally by the admin."
        )


# How long (seconds) to wait for `boundary connect` to emit its JSON line.
_CONNECT_TIMEOUT = int(os.environ.get("BOUNDARY_CONNECT_TIMEOUT", "30"))


def connect(target_id: str) -> dict:
    """
    Open an HCP Boundary session to the given SSH target using credential injection.

    If already connected, returns the existing session info immediately (no-op).

    Uses `boundary connect` which acts as a raw TCP proxy:
    it prints a single JSON line with the local address/port, then stays alive.
    Boundary injects the stored Ubuntu credentials automatically — paramiko
    must NOT supply a password or key.

    Returns:
        dict with keys: host (str), port (int), session_id (str)

    Raises:
        TimeoutError: if the process does not emit the JSON line within
                      BOUNDARY_CONNECT_TIMEOUT seconds (default 30).
        RuntimeError: if the process exits before emitting the JSON line.
    """
    global _session_proc, _session_info

    if is_connected():
        return _session_info  # type: ignore[return-value]

    # Authenticate first (obtains/refreshes the Boundary token)
    authenticate()

    env = {**os.environ, "BOUNDARY_ADDR": os.environ["BOUNDARY_ADDR"]}

    proc = subprocess.Popen(
        [
            "boundary", "connect",
            f"-target-id={target_id}",
            "-format=json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    # Drain stderr in a background thread to prevent the OS pipe buffer from
    # filling up and deadlocking the boundary process before it writes to stdout.
    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    # Read the first stdout line (the JSON connection info) with a timeout so
    # that a hanging boundary process does not block the caller indefinitely.
    assert proc.stdout is not None
    first_line: str | None = None

    def _read_first_line() -> None:
        nonlocal first_line
        first_line = proc.stdout.readline()  # type: ignore[union-attr]

    reader_thread = threading.Thread(target=_read_first_line, daemon=True)
    reader_thread.start()
    reader_thread.join(timeout=_CONNECT_TIMEOUT)

    if reader_thread.is_alive():
        # Timed out — kill the process and surface a clear error.
        proc.kill()
        proc.wait()
        raise TimeoutError(
            f"boundary connect did not respond within {_CONNECT_TIMEOUT}s "
            f"for target '{target_id}'. "
            f"stderr: {''.join(stderr_lines).strip() or '(empty)'}"
        )

    if not first_line:
        # Process exited without writing anything useful.
        rc = proc.wait()
        raise RuntimeError(
            f"boundary connect exited with code {rc} before emitting connection info. "
            f"stderr: {''.join(stderr_lines).strip() or '(empty)'}"
        )

    data = json.loads(first_line)

    # Extract injected credentials if Boundary provided them
    injected_password: str | None = None
    injected_username: str | None = None
    for cred in data.get("credentials", []):
        secret = cred.get("credential") or cred.get("secret", {}).get("decoded", {})
        if "password" in secret:
            injected_password = secret["password"]
        if "username" in secret:
            injected_username = secret["username"]

    _session_proc = proc
    _session_info = {
        "host": data["address"],
        "port": int(data["port"]),
        "session_id": data.get("session_id", ""),
        "injected_password": injected_password,
        "injected_username": injected_username,
    }
    return _session_info


def disconnect() -> None:
    """
    Terminate the Boundary proxy process and reset session state.
    Safe to call when not connected (no-op).
    """
    global _session_proc, _session_info

    if _session_proc is None:
        return

    _session_proc.terminate()
    try:
        _session_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _session_proc.kill()

    _session_proc = None
    _session_info = None


# ---------------------------------------------------------------------------
# Local Boundary CLI execution
# ---------------------------------------------------------------------------

# Boundary subcommands that are read-only / safe to run without extra confirmation.
# Any subcommand not in this set is rejected.
_BOUNDARY_ALLOWED_SUBCOMMANDS: frozenset[str] = frozenset([
    "sessions", "targets", "hosts", "host-sets", "host-catalogs",
    "scopes", "users", "groups", "roles", "auth-methods", "auth-tokens",
    "accounts", "managed-groups", "workers", "credentials",
    "credential-stores", "credential-libraries",
])

# Boundary actions that mutate state — blocked even for allowed subcommands.
_BOUNDARY_BLOCKED_ACTIONS: frozenset[str] = frozenset([
    "create", "update", "delete", "set-grants", "add-grants", "remove-grants",
    "set-principals", "add-principals", "remove-principals",
    "set-hosts", "add-hosts", "remove-hosts",
    "set-host-sets", "add-host-sets", "remove-host-sets",
    "authorize-session", "cancel",
])

_BOUNDARY_CMD_TIMEOUT = int(os.environ.get("BOUNDARY_CMD_TIMEOUT", "15"))


def run_boundary_command(args: list[str]) -> dict:
    """
    Run a read-only Boundary CLI command locally and return its output.

    Only subcommands in _BOUNDARY_ALLOWED_SUBCOMMANDS are permitted.
    Mutating actions (_BOUNDARY_BLOCKED_ACTIONS) are rejected regardless
    of the subcommand.

    Args:
        args: CLI arguments after 'boundary', e.g. ['sessions', 'list', '-format=json']

    Returns:
        dict with keys: stdout (str), stderr (str), exit_code (int)

    Raises:
        ValueError: if the subcommand or action is not permitted.
        TimeoutError: if the command does not complete within BOUNDARY_CMD_TIMEOUT seconds.
    """
    if not args:
        raise ValueError("No Boundary subcommand provided.")

    subcommand = args[0].lstrip("-")
    if subcommand not in _BOUNDARY_ALLOWED_SUBCOMMANDS:
        raise ValueError(
            f"Boundary subcommand '{subcommand}' is not permitted. "
            f"Allowed: {sorted(_BOUNDARY_ALLOWED_SUBCOMMANDS)}"
        )

    # Check all positional tokens for blocked actions
    for token in args[1:]:
        if not token.startswith("-") and token in _BOUNDARY_BLOCKED_ACTIONS:
            raise ValueError(
                f"Boundary action '{token}' is blocked. "
                "Only read-only actions (list, read) are permitted."
            )

    # Always inject -format=json if not already present
    if not any(a.startswith("-format") for a in args):
        args = list(args) + ["-format=json"]

    env = {**os.environ}
    result = subprocess.run(
        ["boundary"] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=_BOUNDARY_CMD_TIMEOUT,
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.returncode,
    }


# Ensure the session is cleaned up if Python exits unexpectedly
atexit.register(disconnect)
