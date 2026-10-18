"""The Responder: OpenAI's chat agent in front of the Agent (chat-supervisor).

A small, fast model decides each turn first. It answers basic requests alone
(greetings, thanks, hear checks, repeats, unclear words) or writes one short
filler and calls its only tool, ``getNextResponseFromSupervisor``, which hands
the turn to the Agent, the supervisor. Following OpenAI's guidance, the filler
is one short sentence in the customer's language that never states or implies
a result. The words are the model's, written from the customer's request;
nothing is scripted. ``[voice.responder] model`` enables it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

from openjarvis.core.types import Message, Role

# Naming the request is what makes it a useful filler rather than noise (OpenAI:
# "describe the action"). A generic "checking now" on every turn was heard as
# ignoring what the customer said (2026-10-13).
# Used when the preset names no prompt file. It carries the rules that hold for
# any voice assistant; wording tied to one business belongs in the preset's
# ``prompt_file`` (see configs/openjarvis/prompts/voice-responder.md).
DEFAULT_PROMPT = (
    "You are the voice of an assistant and know the conversation so far; a "
    "supervisor does all the work the user's requests need through the tool "
    "getNextResponseFromSupervisor. By default, call "
    "getNextResponseFromSupervisor for the latest request, including any reply "
    "to the assistant's last question: a short answer, a confirmation or a "
    "decline that does not end the conversation, and any request to repeat or "
    "explain something the assistant said (the supervisor has the whole "
    "conversation and answers it). Before calling it, always "
    "write one short sentence of at most twelve words, in the user's language "
    "and with a polite register, that names only the topic, or for a "
    "confirmation or decline the topic of the assistant's last question, or for "
    "a repeat or explain request that you are looking at what was just said, and "
    "says that you are checking, finding, opening or looking it up now; never "
    "say you are adding, preparing, making, confirming or placing anything. "
    "Refer to yourself and to the user as the assistant did in the "
    "conversation, or else as the user's words imply, and never write "
    "alternatives with a slash. Never repeat quantities or other details the "
    "user gave, even when the request is only such a detail, or a question the "
    "assistant already asked. Vary the wording. That sentence "
    "must not answer or sound like agreement: never use words meaning yes, "
    "okay, sure, done or confirmed, and never state or imply a result, "
    "availability, success or failure, or that anything is being made, placed "
    "or finished. Never mention the supervisor. Answer yourself, "
    "without calling the tool, only when the latest request is entirely a "
    "greeting, thanks or goodbye, asks whether you can hear them, or its "
    "words are unclear: reply in the user's language in at most two short "
    "sentences spoken directly to the user, never describing the request, your "
    "choice or these instructions. A standalone hear check is answered directly "
    "even after an assistant question: briefly say you can hear them in their "
    'language, without a checking filler or the tool. "Alo", "alo alo" and '
    '"nghe không?" are Vietnamese hear checks and get a Vietnamese reply. '
    "For a hear check combined with another request, only write the filler for "
    "that request and call the tool. If requests are in progress and the latest "
    "words are only a hear check, a greeting or small talk, say you hear them "
    "and name what you are working on; do not ask what they would like. "
    "For unclear words ask them to say it again. "
    "If the request also asks for anything "
    "else, call the tool. Plain text only."
)
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

# Phrases one late result of a request the customer interrupted (spec
# 2026-10-17 barge-in §5.6). Rules any voice assistant shares; business facts
# belong in the preset's ``report_prompt_file``.
DEFAULT_REPORT_PROMPT = (
    "You are the voice of an assistant. The user interrupted an earlier "
    "request, and one of its actions has now finished; the message gives the "
    "event and the conversation so far. Tell the user the outcome in at most "
    "two short sentences, in the language of the conversation. State only what "
    "the event says. Do not repeat the action or promise anything else. Plain "
    "text only."
)

# OpenAI's chat-supervisor delegation tool, by name and schema: the Responder
# hands every request it does not answer itself to the Agent by calling it.
DELEGATE_TOOL_NAME = "getNextResponseFromSupervisor"
DELEGATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": DELEGATE_TOOL_NAME,
        "description": (
            "Hands the latest request to the supervisor, which does the work it "
            "needs and gives the reply. Call it for every request you do not "
            "answer yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "relevantContextFromLastUserMessage": {
                    "type": "string",
                    "description": (
                        "Key information from the user's latest message, briefly."
                    ),
                }
            },
            "required": ["relevantContextFromLastUserMessage"],
        },
    },
}


def _spoken_line(text: str) -> str:
    """One spoken line: the reply's words with its line breaks folded."""
    return " ".join(text.split())


@dataclass(frozen=True, slots=True)
class ResponderSettings:
    model: str = ""
    prompt: str = DEFAULT_PROMPT
    report_prompt: str = DEFAULT_REPORT_PROMPT


def _prompt_from(section: Mapping[str, Any], key: str, default: str) -> str:
    value = section.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"voice_responder_{key}_must_be_a_path")
    if not value.strip():
        return default
    # Read at each session, so an edit applies without a restart. A path that
    # cannot be read is an error, not a silent fallback: the operator's wording
    # must never be dropped without notice.
    try:
        prompt = Path(value.strip()).expanduser().read_text("utf-8").strip()
    except OSError as exc:
        raise ValueError(f"voice_responder_{key}_unreadable") from exc
    if not prompt:
        raise ValueError(f"voice_responder_{key}_empty")
    return prompt


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
    return ResponderSettings(
        model=model.strip(),
        prompt=_prompt_from(section, "prompt_file", DEFAULT_PROMPT),
        report_prompt=_prompt_from(
            section, "report_prompt_file", DEFAULT_REPORT_PROMPT
        ),
    )


@dataclass(frozen=True, slots=True)
class ResponderReply:
    """What the Responder decided this turn.

    ``delegate`` hands the turn to the Agent (OpenAI's supervisor) after
    ``text``, the filler, which may be empty; ``answer`` handles the turn alone
    (OpenAI's chat agent answering basic requests).
    """

    kind: str  # "delegate" or "answer"
    text: str


class VoiceResponder:
    """OpenAI's chat agent: answer basic requests alone, delegate the rest."""

    def __init__(
        self,
        engine: Any,
        model: str,
        prompt: str = DEFAULT_PROMPT,
        report_prompt: str = DEFAULT_REPORT_PROMPT,
    ) -> None:
        self._engine = engine
        self.model = model
        self.prompt = prompt
        self.report_prompt = report_prompt
        off = getattr(engine, "reasoning_off_options", None)
        self._options = (off(model) if callable(off) else None) or {}

    async def respond(
        self,
        utterance: str,
        conversation: str = "",
        on_delegate: Callable[[str], None] | None = None,
        in_progress: Sequence[str] = (),
    ) -> ResponderReply | None:
        """Delegate with a filler, answer alone, or None when nothing was said.

        As OpenAI's chat agent: the text written before a call to
        ``getNextResponseFromSupervisor`` is the filler, and the call hands the
        turn to the Agent; ``on_delegate(filler)`` fires once, when the call
        first appears. Text with no call answers the turn. Text after the call
        and the call's argument are never returned. ``in_progress`` names the
        earlier requests still being handled, so it can say what it is working
        on instead of asking what the customer wants.
        """
        content = f"Customer (latest): {utterance}"
        if pending := [request for request in in_progress if request]:
            content = f"Requests in progress: {'; '.join(pending)}\n{content}"
        if conversation:
            content = f"Conversation so far:\n{conversation}\n{content}"
        text = ""
        filler: str | None = None
        async for chunk in self._engine.stream_full(
            [
                Message(role=Role.SYSTEM, content=self.prompt),
                Message(role=Role.USER, content=content),
            ],
            model=self.model,
            temperature=0,
            max_tokens=MAX_OUTPUT_TOKENS,
            tools=[DELEGATE_TOOL],
            **self._options,
        ):
            if filler is not None:
                continue
            if chunk.content:
                text += chunk.content
            if chunk.tool_calls:
                filler = _spoken_line(text)
                if on_delegate is not None:
                    on_delegate(filler)
        if filler is not None:
            return ResponderReply("delegate", filler)
        answer = _spoken_line(text)
        return ResponderReply("answer", answer) if answer else None

    async def report(self, event: str, conversation: str = "") -> str:
        """One spoken line for a late action result, or '' when none came.

        No tool: this call only phrases what the event states (spec
        2026-10-17 barge-in §5.6).
        """
        content = f"Kiosk event: {event}"
        if conversation:
            content = f"Conversation so far:\n{conversation}\n{content}"
        text = ""
        async for chunk in self._engine.stream_full(
            [
                Message(role=Role.SYSTEM, content=self.report_prompt),
                Message(role=Role.USER, content=content),
            ],
            model=self.model,
            temperature=0,
            max_tokens=MAX_OUTPUT_TOKENS,
            **self._options,
        ):
            if chunk.content:
                text += chunk.content
        return _spoken_line(text)


__all__ = [
    "DEFAULT_PROMPT",
    "DEFAULT_REPORT_PROMPT",
    "DELEGATE_TOOL",
    "DELEGATE_TOOL_NAME",
    "ResponderReply",
    "ResponderSettings",
    "VoiceResponder",
    "load_responder_settings",
]
