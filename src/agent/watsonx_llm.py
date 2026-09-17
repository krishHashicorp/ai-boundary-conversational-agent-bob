"""
watsonx_llm.py
Thin wrapper around the WatsonX.ai ModelInference API for chat.

Uses the SDK's native chat() method (POST /ml/v1/text/chat) so prompt
formatting is handled by the SDK per-model — no manual chat templates needed.
"""

import os
import time
import random
from dotenv import load_dotenv
from ibm_watsonx_ai import Credentials
from ibm_watsonx_ai.foundation_models import ModelInference

load_dotenv()

SYSTEM_PROMPT = (
    "You are a Linux infrastructure assistant. "
    "You can connect to a remote Ubuntu host through HCP Boundary and run shell commands on it. "
    "You maintain a persistent SSH session: connect once, issue as many commands as needed, "
    "and only disconnect when the user explicitly asks or says they are done. "
    "Always reason step by step and use the available tools to gather information before answering."
)

_model: ModelInference | None = None


def _get_model() -> ModelInference:
    """Initialise and cache the WatsonX.ai model client."""
    global _model
    if _model is None:
        credentials = Credentials(
            api_key=os.environ["WATSONX_API_KEY"],
            url=os.environ["WATSONX_URL"],
        )
        _model = ModelInference(
            model_id=os.environ.get("WATSONX_MODEL_ID", "meta-llama/llama-3-3-70b-instruct"),
            credentials=credentials,
            project_id=os.environ["WATSONX_PROJECT_ID"],
            params={
                "max_new_tokens": 1024,
                "temperature": 0,
            },
        )
    return _model


# Retry settings for 429 / consumption_limit_reached responses
_MAX_RETRIES = 5
_BACKOFF_BASE = 5   # seconds — doubled on each retry
_BACKOFF_CAP  = 60  # maximum wait between retries


def chat(messages: list[dict]) -> str:
    """
    Send a conversation history to WatsonX.ai and return the assistant reply.

    Prepends a system message if the caller hasn't supplied one.
    Uses the SDK's /ml/v1/text/chat endpoint — no manual prompt templating.
    Retries up to _MAX_RETRIES times with exponential backoff on 429 errors.

    Args:
        messages: list of {"role": "user"|"assistant"|"system", "content": str}

    Returns:
        The model's response as a stripped string.
    """
    # Ensure a system message is present
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(messages)

    model = _get_model()

    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = model.chat(messages=messages)
            return response["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            err = str(exc)
            is_rate_limit = "429" in err or "consumption_limit_reached" in err
            if is_rate_limit and attempt < _MAX_RETRIES:
                wait = min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP)
                # Add ±20 % jitter to avoid thundering-herd when multiple
                # sub-tasks retry at the same moment
                wait = wait * (0.8 + 0.4 * random.random())
                print(f"[watsonx] Rate limit hit — retrying in {wait:.1f}s "
                      f"(attempt {attempt + 1}/{_MAX_RETRIES}) …")
                time.sleep(wait)
            else:
                raise


if __name__ == "__main__":
    reply = chat([{"role": "user", "content": "Hello, who are you?"}])
    print(reply)
