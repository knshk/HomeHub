"""Per-model tuning: the knobs an admin can set to shape how a model answers.

Stored per model in ``managed_models.tuning_json`` and merged into the outbound
request by ``openai_routes``. Everything here is pure (no I/O), so the clamping
and precedence rules are unit-tested directly.

Design notes
------------
* **Per-request wins.** A value the caller explicitly sent is never overwritten
  by the stored tuning — tuning supplies defaults, it does not seize control.
  The two exceptions are structural and documented on the fields themselves:
  ``system`` (persona) and ``history_turns`` (context trimming).
* **Only knobs the upstream actually honours.** These map onto the OpenAI-
  compatible surface both Ollama and the cloud providers implement. Ollama's
  native-only options (``num_ctx``, ``num_thread``, ``repeat_penalty``) are NOT
  exposed here: they are ignored by the OpenAI endpoint, and offering a control
  that silently does nothing is worse than not offering it.
* **history_turns is the latency knob.** Every turn re-sends the whole
  conversation, and a CPU-bound model must re-read all of it before it can emit
  a single token, so first-response time grows with the chat. Capping the
  history bounds that prefill.
"""
from __future__ import annotations

from typing import Any, Dict, List

# name -> spec. `kind` drives both validation here and the control the UI draws.
SCHEMA: Dict[str, Dict[str, Any]] = {
    "system": {
        "kind": "text", "max_len": 4000, "default": "",
        "label": "Persona / system prompt",
        "help": "Sets how the assistant behaves. Replaces the caller's system message.",
    },
    "temperature": {
        "kind": "float", "min": 0.0, "max": 2.0, "default": None,
        "label": "Temperature",
        "help": "Lower is more focused and repeatable; higher is more varied.",
    },
    "top_p": {
        "kind": "float", "min": 0.0, "max": 1.0, "default": None,
        "label": "Top-p",
        "help": "Nucleus sampling. Usually tune this or temperature, not both.",
    },
    "max_tokens": {
        "kind": "int", "min": 16, "max": 8192, "default": None,
        "label": "Max reply length",
        "help": "Caps how long an answer can get. Shorter replies finish sooner.",
    },
    "history_turns": {
        "kind": "int", "min": 0, "max": 50, "default": None,
        "label": "History sent",
        "help": "How many recent messages to send (0 = the whole conversation). "
                "Fewer means less to re-read, so the first words arrive sooner.",
    },
    "presence_penalty": {
        "kind": "float", "min": -2.0, "max": 2.0, "default": None,
        "label": "Presence penalty",
        "help": "Positive values push the model toward new topics.",
    },
    "frequency_penalty": {
        "kind": "float", "min": -2.0, "max": 2.0, "default": None,
        "label": "Frequency penalty",
        "help": "Positive values reduce repetition.",
    },
    "seed": {
        "kind": "int", "min": 0, "max": 2 ** 31 - 1, "default": None,
        "label": "Seed",
        "help": "Fix for repeatable answers; leave empty for varied ones.",
    },
}

# Keys forwarded verbatim onto the request body (system/history are structural).
_PASSTHROUGH = ("temperature", "top_p", "max_tokens",
                "presence_penalty", "frequency_penalty", "seed")


def _clamp_number(value: Any, spec: Dict[str, Any], as_int: bool) -> Any:
    try:
        num = int(value) if as_int else float(value)
    except (TypeError, ValueError):
        raise ValueError("must be a number")
    lo, hi = spec["min"], spec["max"]
    return max(lo, min(hi, num))


def sanitize(raw: Any) -> Dict[str, Any]:
    """Validate and clamp a tuning dict. Unknown keys are dropped; blank values
    mean "unset" and are removed, so a cleared field returns to the default."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("tuning must be an object")

    out: Dict[str, Any] = {}
    for name, value in raw.items():
        spec = SCHEMA.get(name)
        if spec is None:
            continue                      # unknown knob — ignore rather than fail
        if value is None or value == "":
            continue                      # explicitly cleared
        if spec["kind"] == "text":
            text = str(value).strip()
            if not text:
                continue
            out[name] = text[: spec["max_len"]]
        elif spec["kind"] == "int":
            out[name] = _clamp_number(value, spec, as_int=True)
        else:
            out[name] = _clamp_number(value, spec, as_int=False)
    return out


def trim_history(messages: List[Dict[str, Any]], turns: int) -> List[Dict[str, Any]]:
    """Keep the most recent `turns` non-system messages, preserving any leading
    system message. `turns` <= 0 means keep everything."""
    if not isinstance(messages, list) or turns <= 0 or len(messages) <= turns:
        return messages
    system = [m for m in messages[:1] if isinstance(m, dict) and m.get("role") == "system"]
    rest = messages[len(system):]
    return system + rest[-turns:]


def apply(payload: Dict[str, Any], tuning: Dict[str, Any]) -> Dict[str, Any]:
    """Merge stored tuning into an OpenAI-shaped request body.

    Returns the same dict (mutated) for convenience. Values the caller set
    explicitly are preserved; tuning only fills what is absent.
    """
    if not tuning:
        return payload

    for key in _PASSTHROUGH:
        if key in tuning and key not in payload:
            payload[key] = tuning[key]

    messages = payload.get("messages")
    if isinstance(messages, list):
        # Persona: replace a leading system message, or insert one.
        persona = tuning.get("system")
        if persona:
            if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
                messages = [{"role": "system", "content": persona}] + messages[1:]
            else:
                messages = [{"role": "system", "content": persona}] + messages
        turns = int(tuning.get("history_turns") or 0)
        payload["messages"] = trim_history(messages, turns)

    return payload
