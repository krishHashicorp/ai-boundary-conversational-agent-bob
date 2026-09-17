"""
tests/test_boundary_session.py
Unit tests for the HCP Boundary session manager.
"""

import json
import os
import subprocess
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("BOUNDARY_ADDR", "https://test.boundary.hashicorp.cloud")
os.environ.setdefault("BOUNDARY_AUTH_METHOD", "password")
os.environ.setdefault("BOUNDARY_AUTH_METHOD_ID", "ampw_test")
os.environ.setdefault("BOUNDARY_LOGIN_NAME", "testuser")
os.environ.setdefault("BOUNDARY_PASSWORD", "testpass")
os.environ.setdefault("BOUNDARY_TARGET_ID", "tssh_test")
os.environ.setdefault("BOUNDARY_OIDC_AUTH_METHOD_ID", "amoidc_test")

import src.agent.boundary_session as bs  # noqa: E402


@pytest.fixture(autouse=True)
def reset_session_state():
    """Reset module-level session state before each test."""
    bs._session_proc = None
    bs._session_info = None
    yield
    bs._session_proc = None
    bs._session_info = None


class TestAuthenticate:
    @patch("src.agent.boundary_session.authenticate_password")
    @patch("src.agent.boundary_session.authenticate_oidc")
    def test_skips_auth_when_token_already_set(self, mock_oidc, mock_password):
        os.environ["BOUNDARY_TOKEN"] = "tok_already_present"
        try:
            bs.authenticate()
            mock_password.assert_not_called()
            mock_oidc.assert_not_called()
        finally:
            del os.environ["BOUNDARY_TOKEN"]

    @patch("src.agent.boundary_session.authenticate_password")
    def test_calls_password_auth_when_no_token(self, mock_password):
        os.environ.pop("BOUNDARY_TOKEN", None)
        os.environ["BOUNDARY_AUTH_METHOD"] = "password"
        bs.authenticate()
        mock_password.assert_called_once()


class TestAuthenticateOidc:
    @patch("src.agent.boundary_session.subprocess.run")
    def test_sets_boundary_token_from_stdout(self, mock_run):
        """authenticate_oidc reads the token from stdout JSON (Boundary CLI v0.21+)."""
        import json as _json

        token_data = _json.dumps({"item": {"attributes": {"token": "tok_oidc_xyz"}}})

        mock_result = MagicMock()
        mock_result.stdout = token_data
        mock_run.return_value = mock_result

        os.environ.pop("BOUNDARY_TOKEN", None)

        bs.authenticate_oidc()

        assert os.environ.get("BOUNDARY_TOKEN") == "tok_oidc_xyz"

    @patch("src.agent.boundary_session.subprocess.run")
    def test_stderr_not_redirected(self, mock_run):
        """stderr must be left as None (terminal) so the browser callback URL stays visible."""
        import json as _json

        token_data = _json.dumps({"item": {"attributes": {"token": "tok_oidc_abc"}}})

        def fake_run(cmd, **kwargs):
            # Assert stderr was NOT redirected — browser URL must remain visible
            assert kwargs.get("stderr") is None
            # Assert stdout IS captured so we can read the token JSON
            assert kwargs.get("stdout") == subprocess.PIPE
            mock_result = MagicMock()
            mock_result.stdout = token_data
            return mock_result

        mock_run.side_effect = fake_run
        os.environ.pop("BOUNDARY_TOKEN", None)

        bs.authenticate_oidc()


class TestAuthenticatePassword:
    @patch("src.agent.boundary_session.subprocess.run")
    def test_sets_boundary_token_env_var(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"item": {"attributes": {"token": "tok_test123"}}}),
            returncode=0,
        )
        bs.authenticate_password()
        assert os.environ.get("BOUNDARY_TOKEN") == "tok_test123"

    @patch("src.agent.boundary_session.subprocess.run")
    def test_calls_boundary_cli_with_correct_args(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"item": {"attributes": {"token": "tok_abc"}}}),
            returncode=0,
        )
        bs.authenticate_password()
        args = mock_run.call_args[0][0]
        assert "boundary" in args[0]
        assert "authenticate" in args
        assert "password" in args
        assert any("ampw_test" in a for a in args)


class TestConnect:
    def _make_mock_proc(self, host="127.0.0.1", port=54321, session_id="s_test"):
        session_json = json.dumps({
            "address": host,
            "port": port,
            "session_id": session_id,
        }) + "\n"
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # process running
        mock_proc.stdout = StringIO(session_json)
        return mock_proc

    @patch("src.agent.boundary_session.authenticate")
    @patch("src.agent.boundary_session.subprocess.Popen")
    def test_returns_session_info(self, mock_popen, mock_auth):
        mock_popen.return_value = self._make_mock_proc()
        result = bs.connect("tssh_test")
        assert result["host"] == "127.0.0.1"
        assert result["port"] == 54321
        assert result["session_id"] == "s_test"

    @patch("src.agent.boundary_session.authenticate")
    @patch("src.agent.boundary_session.subprocess.Popen")
    def test_connect_twice_is_noop(self, mock_popen, mock_auth):
        mock_popen.return_value = self._make_mock_proc()
        bs.connect("tssh_test")
        bs.connect("tssh_test")  # second call — should not create a new process
        assert mock_popen.call_count == 1

    @patch("src.agent.boundary_session.authenticate")
    @patch("src.agent.boundary_session.subprocess.Popen")
    def test_is_connected_true_while_proc_running(self, mock_popen, mock_auth):
        mock_popen.return_value = self._make_mock_proc()
        bs.connect("tssh_test")
        assert bs.is_connected() is True


class TestCheckCancelled:
    def test_no_proc_does_not_raise(self):
        """check_cancelled() is silent when there is no active session."""
        bs._session_proc = None
        bs.check_cancelled()  # must not raise

    def test_running_proc_does_not_raise(self):
        """check_cancelled() is silent while the proxy process is still alive."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # process still running
        bs._session_proc = mock_proc
        bs.check_cancelled()  # must not raise

    def test_exited_proc_raises_cancelled_error(self):
        """check_cancelled() raises BoundarySessionCancelledError when the process has exited."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # process exited (e.g. admin cancelled)
        bs._session_proc = mock_proc
        with pytest.raises(bs.BoundarySessionCancelledError, match="cancelled externally by the admin"):
            bs.check_cancelled()

    def test_zero_exit_also_raises(self):
        """Any exited process (even exit 0) is treated as an external cancellation."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        bs._session_proc = mock_proc
        with pytest.raises(bs.BoundarySessionCancelledError):
            bs.check_cancelled()


class TestDisconnect:
    def test_disconnect_when_not_connected_is_noop(self):
        bs.disconnect()  # should not raise

    @patch("src.agent.boundary_session.authenticate")
    @patch("src.agent.boundary_session.subprocess.Popen")
    def test_disconnect_terminates_proc_and_resets_state(self, mock_popen, mock_auth):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        session_json = json.dumps({"address": "127.0.0.1", "port": 9999, "session_id": "s1"}) + "\n"
        mock_proc.stdout = StringIO(session_json)
        mock_popen.return_value = mock_proc

        bs.connect("tssh_test")
        assert bs.is_connected() is True

        bs.disconnect()
        mock_proc.terminate.assert_called_once()
        assert bs._session_proc is None
        assert bs._session_info is None
