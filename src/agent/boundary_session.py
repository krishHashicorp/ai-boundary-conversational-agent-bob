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
from dotenv import load_dotenv

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
    Blocks until the browser flow completes.
    Sets the BOUNDARY_TOKEN environment variable.

    NOTE: stdout must NOT be captured so the boundary CLI can open the
    browser and print the callback URL to the terminal. We redirect only
    stderr to PIPE so we can surface errors without interfering with the
    browser flow. The token JSON is written to a temp file via -output-file.
    """
    import tempfile, pathlib

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        subprocess.run(
            [
                "boundary", "authenticate", "oidc",
                f"-addr={os.environ['BOUNDARY_ADDR']}",
                f"-auth-method-id={os.environ['BOUNDARY_OIDC_AUTH_METHOD_ID']}",
                f"-token-name=none",
                f"-format=json",
                f"-keyring-type=none",
            ],
            stdout=open(tmp_path, "w"),
            text=True,
            check=True,
        )
        data = json.loads(pathlib.Path(tmp_path).read_text())
        token = data["item"]["attributes"]["token"]
        os.environ["BOUNDARY_TOKEN"] = token
    finally:
        pathlib.Path(tmp_path).unlink(missing_ok=True)


def authenticate() -> None:
    """
    Authenticate to HCP Boundary using the method configured by BOUNDARY_AUTH_METHOD
    env var (defaults to 'password'). Dispatches to authenticate_password() or
    authenticate_oidc().
    """
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

    # The first line of stdout is the JSON connection info
    assert proc.stdout is not None
    first_line = proc.stdout.readline()
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


# Ensure the session is cleaned up if Python exits unexpectedly
atexit.register(disconnect)
