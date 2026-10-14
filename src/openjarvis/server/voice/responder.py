"""A fast first sentence while the Agent works (OpenAI's chat-supervisor pattern).

The Agent's first round on a tool request takes seconds: its model reasons and
writes the call before anything can be spoken. A small, fast model voices one
neutral acknowledgement meanwhile, following OpenAI's guidance for voice
preambles: one short sentence in the customer's language that never states or
implies a result, and nothing at all for confirmations, corrections, refusals,
greetings, thanks or unclear words. The words are the model's, written from
the customer's request; nothing is scripted. ``[voice.responder] model``
enables it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

from openjarvis.core.types import Message, Role

# Naming the request is what makes it a preamble rather than filler (OpenAI:
# "describe the action"). A generic "checking now" on every turn was heard as
# ignoring what the customer said (2026-10-13).
# Used when the preset names no prompt file. It carries the rules that hold for
# any voice assistant; wording tied to one business belongs in the preset's
# ``prompt_file`` (see configs/openjarvis/prompts/voice-responder.md).
DEFAULT_PROMPT = (
    "You are the voice of an assistant and know the conversation so far; a "
    "slower assistant does the work the user's requests need. Decide in this "
    "sequence and reply with exactly one line. 1. When the latest request needs "
    "something looked up, changed or done, including a short answer to the "
    'assistant\'s last question that starts the next step, reply "SAY: " and '
    "one short sentence of at most twelve words, in the user's language and "
    "with a polite register, that names only the topic and says what you are "
    "doing about it now, such as checking, finding or opening it. Refer to "
    "yourself and to the user as the assistant did in the conversation, or "
    "else as the user's words imply, and never write alternatives with a "
    "slash. Never repeat quantities or other details, or a question the "
    "assistant already asked. Vary the wording. That sentence must not answer "
    "or sound like agreement: never use words meaning yes, okay, sure or done, "
    "and never state or imply a result, availability, success or failure. "
    "2. Only when the latest request is a greeting, thanks or goodbye, asks "
    "whether you can hear them, asks to repeat or explain something already "
    'said in the conversation, or its words are unclear, reply "ANSWER: " '
    "and the whole reply in at most two short sentences; for unclear words, "
    "ask them to say it again. Never answer anything under 1. 3. Reply exactly "
    "SKIP when the latest request only confirms or declines something without "
    "starting a new step. Plain text only."
)
SKIP = "SKIP"
ANSWER = "ANSWER:"
SAY = "SAY:"
# Told to the Agent on turns the responder runs. Both write at once and neither
# sees the other's words, so without it the reply restated what the
# acknowledgement had just said (2026-10-13).
AGENT_NOTE = (
    "A brief acknowledgement naming the topic of the customer's request may "
    "already have been spoken this turn. Do not open your spoken reply, including "
    "any message you pass to a tool for the customer, with an acknowledgement or "
    "a restatement of the request; start with the result, the next step or your "
    "question."
)
# A ceiling, not a length: the line ends after a few words. A reasoning model
# (gpt-6-luna, no reasoning-off switch) spends part of it first; at 40 its
# Responses stream ended incomplete on 10 of 16 requests (2026-10-13).
MAX_OUTPUT_TOKENS = 400


@dataclass(frozen=True, slots=True)
class ResponderSettings:
    model: str = ""
    prompt: str = DEFAULT_PROMPT


def load_responder_settings() -> ResponderSettings:
    """Load optional ``[voice.responder]`` settings from the active preset."""
    config_value = os.environ.get("OPENJARVIS_CONFIG", "").strip()
    if not config_value:
        return ResponderSettings()
    config_path = Path(config_value).expanduser()
    if not config_path.exists():
        return ResponderSettings()
    with config_path.open("rb") as config_file:
        data = tomllib.load(config_file)
    voice = data.get("voice", {})
    section = voice.get("responder", {}) if isinstance(voice, Mapping) else None
    if not isinstance(section, Mapping):
        raise ValueError("voice_responder_config_must_be_a_table")
    model = section.get("model", "")
    if not isinstance(model, str):
        raise ValueError("voice_responder_model_must_be_a_string")
    prompt = DEFAULT_PROMPT
    prompt_file = section.get("prompt_file", "")
    if not isinstance(prompt_file, str):
        raise ValueError("voice_responder_prompt_file_must_be_a_path")
    if prompt_file.strip():
        # Read at each session, so an edit applies without a restart. A path
        # that cannot be read is an error, not a silent fallback: the
        # operator's wording must never be dropped without notice.
        try:
            prompt = Path(prompt_file.strip()).expanduser().read_text("utf-8").strip()
        except OSError as exc:
            raise ValueError("voice_responder_prompt_file_unreadable") from exc
        if not prompt:
            raise ValueError("voice_responder_prompt_file_empty")
    return ResponderSettings(model=model.strip(), prompt=prompt)


@dataclass(frozen=True, slots=True)
class ResponderReply:
    """What the fast voice says this turn.

    ``answer`` handles the turn itself (OpenAI's chat agent answering basic
    requests); ``preamble`` speaks while the Agent, the supervisor, does the
    work.
    """

    kind: str  # "preamble" or "answer"
    text: str


class VoiceResponder:
    """Ask a fast model for the turn's first words: a preamble, an answer, or none."""

    def __init__(self, engine: Any, model: str, prompt: str = DEFAULT_PROMPT) -> None:
        self._engine = engine
        self.model = model
        self.prompt = prompt
        off = getattr(engine, "reasoning_off_options", None)
        self._options = (off(model) if callable(off) else None) or {}

    async def acknowledgement(
        self, utterance: str, conversation: str = ""
    ) -> ResponderReply | None:
        """The turn's first words for *utterance*, or None when none are said.

        *conversation* is the recent exchange, oldest first, so the reply fits
        what was already asked and said: one voice that knows the whole
        conversation, as in OpenAI's chat-supervisor pattern.
        """
        content = f"Customer (latest): {utterance}"
        if conversation:
            content = f"Conversation so far:\n{conversation}\n{content}"
        text = ""
        async for chunk in self._engine.stream_full(
            [
                Message(role=Role.SYSTEM, content=self.prompt),
                Message(role=Role.USER, content=content),
            ],
            model=self.model,
            temperature=0.3,
            max_tokens=MAX_OUTPUT_TOKENS,
            **self._options,
        ):
            if chunk.content:
                text += chunk.content
        line = text.strip().splitlines()[0].strip() if text.strip() else ""
        if not line or line.upper().startswith(SKIP):
            return None
        if line.upper().startswith(ANSWER):
            answer = line[len(ANSWER) :].strip()
            return ResponderReply("answer", answer) if answer else None
        if line.upper().startswith(SAY):
            line = line[len(SAY) :].strip()
        return ResponderReply("preamble", line) if line else None


__all__ = [
    "DEFAULT_PROMPT",
    "ResponderReply",
    "ResponderSettings",
    "VoiceResponder",
    "load_responder_settings",
]
