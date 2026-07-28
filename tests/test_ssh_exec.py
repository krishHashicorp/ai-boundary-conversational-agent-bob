"""
tests/test_ssh_exec.py
Unit tests for the persistent SSH connection manager.

The module now uses paramiko.Transport + an interactive shell channel
rather than SSHClient. Tests mock at the Transport/Channel level.
"""

import os
import socket
from unittest.mock import MagicMock, patch, call

import pytest

os.environ.setdefault("SSH_USERNAME", "ubuntu")

import src.agent.ssh_exec as se  # noqa: E402


@pytest.fixture(autouse=True)
def reset_ssh_state():
    """Reset module-level transport/shell state before each test."""
    se._transport = None
    se._shell = None
    yield
    se._transport = None
    se._shell = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_active_transport() -> MagicMock:
    t = MagicMock()
    t.is_active.return_value = True
    return t


def _make_active_shell() -> MagicMock:
    ch = MagicMock()
    ch.closed = False
    return ch


# ---------------------------------------------------------------------------
# is_connected
# ---------------------------------------------------------------------------

class TestIsConnected:
    def test_false_when_no_transport(self):
        assert se.is_connected() is False

    def test_false_when_shell_is_none(self):
        se._transport = _make_active_transport()
        se._shell = None
        assert se.is_connected() is False

    def test_false_when_transport_inactive(self):
        t = MagicMock()
        t.is_active.return_value = False
        se._transport = t
        se._shell = _make_active_shell()
        assert se.is_connected() is False

    def test_false_when_shell_closed(self):
        se._transport = _make_active_transport()
        ch = MagicMock()
        ch.closed = True
        se._shell = ch
        assert se.is_connected() is False

    def test_true_when_transport_active_and_shell_open(self):
        se._transport = _make_active_transport()
        se._shell = _make_active_shell()
        assert se.is_connected() is True


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------

class TestConnect:
    @patch("src.agent.ssh_exec._read_until_prompt", return_value="$ ")
    @patch("src.agent.ssh_exec.socket.create_connection")
    @patch("src.agent.ssh_exec.paramiko.Transport")
    def test_connect_uses_auth_none(self, mock_transport_class, mock_sock, mock_drain):
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = True
        mock_transport.auth_none.return_value = []
        mock_transport_class.return_value = mock_transport

        mock_channel = MagicMock()
        mock_channel.closed = False
        mock_transport.open_session.return_value = mock_channel

        se.connect("127.0.0.1", 54321, "ubuntu")

        mock_transport.auth_none.assert_called_once_with("ubuntu")
        assert se._transport is mock_transport
        assert se._shell is mock_channel

    @patch("src.agent.ssh_exec._read_until_prompt", return_value="$ ")
    @patch("src.agent.ssh_exec.socket.create_connection")
    @patch("src.agent.ssh_exec.paramiko.Transport")
    def test_connect_twice_is_noop(self, mock_transport_class, mock_sock, mock_drain):
        mock_transport = _make_active_transport()
        mock_transport.auth_none.return_value = []
        mock_transport_class.return_value = mock_transport

        mock_channel = _make_active_shell()
        mock_transport.open_session.return_value = mock_channel

        se.connect("127.0.0.1", 54321, "ubuntu")
        se.connect("127.0.0.1", 54321, "ubuntu")  # second call — no-op

        assert mock_transport_class.call_count == 1

    @patch("src.agent.ssh_exec._read_until_prompt", return_value="$ ")
    @patch("src.agent.ssh_exec.socket.create_connection")
    @patch("src.agent.ssh_exec.paramiko.Transport")
    def test_falls_back_to_password_auth(self, mock_transport_class, mock_sock, mock_drain):
        import paramiko
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = True
        mock_transport.auth_none.side_effect = paramiko.BadAuthenticationType(
            "bad", ["password"]
        )
        mock_transport_class.return_value = mock_transport

        mock_channel = _make_active_shell()
        mock_transport.open_session.return_value = mock_channel

        se.connect("127.0.0.1", 54321, "ubuntu", password="secret")

        mock_transport.auth_password.assert_called_once_with("ubuntu", "secret")


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------

class TestRunCommand:
    def test_raises_if_not_connected(self):
        with pytest.raises(RuntimeError, match="not connected"):
            se.run_command("whoami")

    def test_returns_stdout_and_exit_code(self):
        sentinel_holder: list[str] = []
        original_run = se.run_command

        mock_shell = _make_active_shell()
        se._transport = _make_active_transport()
        se._shell = mock_shell

        # Capture what was sent and simulate a realistic response
        def fake_sendall(data: bytes) -> None:
            text = data.decode()
            # Extract sentinel from "command ; echo SENTINEL$?\n"
            parts = text.split("echo ")
            if len(parts) > 1:
                sentinel = parts[1].split("$?")[0].strip()
                sentinel_holder.append(sentinel)

        mock_shell.sendall.side_effect = fake_sendall

        # _read_until_prompt returns a fake terminal output
        with patch("src.agent.ssh_exec._read_until_prompt") as mock_read:
            def fake_read(ch, timeout=30.0):
                if not sentinel_holder:
                    return "$ "
                s = sentinel_holder[0]
                return f"whoami ; echo {s}$?\r\nmock-ai-agent-linux\r\n{s}0\r\n$ "

            mock_read.side_effect = fake_read

            result = se.run_command("whoami")

        assert result["stdout"] == "mock-ai-agent-linux"
        assert result["exit_code"] == 0
        assert result["stderr"] == ""

    def test_captures_non_zero_exit_code(self):
        sentinel_holder: list[str] = []

        mock_shell = _make_active_shell()
        se._transport = _make_active_transport()
        se._shell = mock_shell

        def fake_sendall(data: bytes) -> None:
            text = data.decode()
            parts = text.split("echo ")
            if len(parts) > 1:
                sentinel = parts[1].split("$?")[0].strip()
                sentinel_holder.append(sentinel)

        mock_shell.sendall.side_effect = fake_sendall

        with patch("src.agent.ssh_exec._read_until_prompt") as mock_read:
            def fake_read(ch, timeout=30.0):
                if not sentinel_holder:
                    return "$ "
                s = sentinel_holder[0]
                return (
                    f"ls /bad ; echo {s}$?\r\n"
                    f"ls: cannot access '/bad': No such file or directory\r\n"
                    f"{s}2\r\n$ "
                )

            mock_read.side_effect = fake_read

            result = se.run_command("ls /bad")

        assert result["exit_code"] == 2
        assert "No such file or directory" in result["stdout"]


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------

class TestDisconnect:
    def test_disconnect_when_not_connected_is_noop(self):
        se.disconnect()  # should not raise

    def test_disconnect_closes_shell_and_transport(self):
        mock_transport = _make_active_transport()
        mock_shell = _make_active_shell()
        se._transport = mock_transport
        se._shell = mock_shell

        se.disconnect()

        mock_shell.close.assert_called_once()
        mock_transport.close.assert_called_once()
        assert se._transport is None
        assert se._shell is None
