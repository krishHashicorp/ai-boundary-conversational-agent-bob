"""
ssh_exec.py
Persistent SSH connection manager using paramiko.

Connects through the local Boundary proxy port. The Boundary proxy performs
credential injection at the Worker layer and accepts anonymous SSH auth
(auth_none) — no password or key is needed from the client side.

Session model:
- connect() opens a Transport, authenticates, and requests a PTY + interactive
  shell once. All run_command() calls send over that single persistent shell
  channel — state (cwd, env vars, etc.) is preserved between commands and
  Boundary session recording sees one continuous interactive session.
- disconnect() closes the channel + transport and resets state.
"""

import re
import socket
import time
import uuid
import paramiko
from dotenv import load_dotenv

load_dotenv()

# Module-level state
_transport: paramiko.Transport | None = None
_shell: paramiko.Channel | None = None

_READ_TIMEOUT = 30.0
_READ_CHUNK = 4096

# Matches ANSI/VT100 escape sequences
_ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")

# Matches a shell prompt line: anything ending in "$ " (bash/sh/custom prompts)
_PROMPT_RE = re.compile(r"^.*\$\s*$", re.MULTILINE)


def _strip_ansi(text: str) -> str:
    """Remove all ANSI/VT100 escape sequences from text."""
    return _ANSI_ESCAPE.sub("", text)


def is_connected() -> bool:
    """Return True if the persistent shell channel is open and active."""
    return (
        _transport is not None
        and _transport.is_active()
        and _shell is not None
        and not _shell.closed
    )


def connect(host: str, port: int, username: str, password: str | None = None) -> None:
    """
    Open a persistent interactive shell through the Boundary local proxy.

    Requests a PTY so Boundary session recording captures a single continuous
    interactive session rather than discrete exec invocations. Shell state
    (working directory, environment variables, etc.) persists across all
    subsequent run_command() calls.

    Uses auth_none — Boundary injects credentials at the Worker layer. Falls
    back to password / keyboard-interactive if the proxy requires explicit auth.

    If already connected, this is a no-op.

    Args:
        host:     Proxy address from boundary_session.connect() (always 127.0.0.1)
        port:     Proxy port from boundary_session.connect()
        username: Ubuntu login name (used in the SSH handshake)
        password: Injected password from Boundary JSON (fallback auth only)
    """
    global _transport, _shell

    if is_connected():
        return

    # --- Transport + auth ---
    sock = socket.create_connection((host, port))
    t = paramiko.Transport(sock)
    t.connect()

    try:
        t.auth_none(username)
    except paramiko.BadAuthenticationType as e:
        remaining = e.allowed_types
        if "password" in remaining and password:
            t.auth_password(username, password)
        elif "keyboard-interactive" in remaining and password:
            t.auth_interactive(username, lambda title, instr, fields: [password] * len(fields))
        else:
            raise RuntimeError(
                f"Boundary proxy requires auth methods {remaining} but no password is available."
            ) from e

    # --- Open interactive shell with PTY ---
    ch = t.open_session()
    ch.get_pty(term="dumb", width=220, height=50)
    ch.invoke_shell()

    # Drain banner / MOTD — wait until the shell prompt appears
    _read_until_prompt(ch, timeout=10.0)

    # Silence any PROMPT_COMMAND that prints metadata to the terminal
    # (common in cloud images that echo instance info before each prompt)
    ch.sendall(b"PROMPT_COMMAND=''\n")
    _read_until_prompt(ch, timeout=5.0)

    _transport = t
    _shell = ch


def _read_until_prompt(ch: paramiko.Channel, timeout: float = _READ_TIMEOUT) -> str:
    """
    Read from the channel until a shell prompt line (ending in '$ ') appears or timeout.
    Returns the full ANSI-stripped output including the prompt line.
    """
    buf = ""
    deadline = time.monotonic() + timeout
    ch.settimeout(0.3)
    while time.monotonic() < deadline:
        try:
            chunk = ch.recv(_READ_CHUNK)
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")
        except socket.timeout:
            pass
        cleaned = _strip_ansi(buf)
        if _PROMPT_RE.search(cleaned):
            return cleaned
    return _strip_ansi(buf)


def _extract_output(raw: str, command: str) -> tuple[str, int]:
    """
    Parse the raw terminal output of a command run.

    Uses a unique sentinel embedded in the output to extract the exit code,
    and strips the prompt lines and command echo from the result.

    Returns:
        (output_text, exit_code)
    """
    lines = raw.splitlines()
    output_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Skip the command echo (terminal echo of what we typed)
        if stripped == command.strip():
            continue
        # Skip prompt lines
        if _PROMPT_RE.match(stripped):
            continue
        output_lines.append(line)

    return "\n".join(output_lines).strip(), 0


def run_command(command: str) -> dict:
    """
    Execute a shell command on the persistent interactive shell and return output.

    Sends the command followed by an exit-code probe (a second echo command),
    reads until the shell prompt reappears, then strips prompts and command
    echoes from the output.

    Args:
        command: Shell command to run on the remote host.

    Returns:
        dict with keys:
            stdout (str):    Command output (stdout + stderr merged by PTY), ANSI-stripped
            stderr (str):    Always empty — PTY merges stderr into stdout
            exit_code (int): Exit status of the command
    """
    if _shell is None or not is_connected():
        raise RuntimeError("SSH shell is not connected. Call connect() first.")

    assert _shell is not None

    # Unique sentinel to capture exit code cleanly
    sentinel = f"__X{uuid.uuid4().hex}__"

    # Send command, then immediately echo sentinel+exit_code on the same line
    _shell.sendall(f"{command} ; echo {sentinel}$?\n".encode())

    # Read until the prompt appears after the sentinel line
    raw = _read_until_prompt(_shell, timeout=_READ_TIMEOUT)

    lines = raw.splitlines()

    # The terminal echoes the full sent line: "command ; echo sentinel$?"
    # Skip the first line that starts with the command text (the echo)
    start_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith(command.strip()[:30]):
            start_idx = i + 1
            break

    # Find the sentinel line and extract exit code
    exit_code = 0
    found = False
    end_idx = len(lines)
    for i in range(start_idx, len(lines)):
        if sentinel in lines[i]:
            try:
                exit_code = int(lines[i].strip().replace(sentinel, "").strip())
            except ValueError:
                exit_code = 0
            found = True
            end_idx = i
            break

    output_lines = lines[start_idx:end_idx]

    return {
        "stdout": "\n".join(output_lines).strip(),
        "stderr": "",   # PTY merges stderr into stdout
        "exit_code": exit_code if found else 0,
    }


def disconnect() -> None:
    """
    Close the interactive shell channel and SSH transport.
    Safe to call when not connected (no-op).
    """
    global _transport, _shell

    if _shell is not None:
        try:
            _shell.close()
        except Exception:
            pass
        _shell = None

    if _transport is not None:
        try:
            _transport.close()
        except Exception:
            pass
        _transport = None
