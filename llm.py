"""
LLM access for the agents.

LLM_PROVIDER=foundry  -> Microsoft Foundry (FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL; auth via `az login`)
LLM_PROVIDER=mock     -> deterministic offline model for development and tests (default; no cost)

The mock answers the same JSON tasks as the real model, using only the facts it is given, and can
simulate failures (MOCK_MODE=fail|slow|bad_json|hallucinate|wrong_name|off_topic|contradict) to exercise
the agents' fallbacks.
"""

import asyncio
import json
import logging
import os

from agent_framework import BaseChatClient, ChatResponse, Message
from dotenv import load_dotenv

from agents import templates

load_dotenv()
logging.getLogger("agent_framework").setLevel(logging.ERROR)


def get_chat_client(provider=None, **mock_kwargs):
    provider = provider or os.getenv("LLM_PROVIDER", "mock")
    if provider == "foundry":
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential
        return FoundryChatClient(credential=DefaultAzureCredential())
    return MockChatClient(**mock_kwargs)


class MockChatClient(BaseChatClient):
    """Offline stand-in for a Foundry model. Reads the JSON task in the last user message."""

    def __init__(self, mode=None, latency_s=None, **kwargs):
        super().__init__(**kwargs)
        self.mode = mode or os.getenv("MOCK_MODE", "ok")
        self.latency_s = float(latency_s if latency_s is not None else os.getenv("MOCK_LATENCY_S", "0"))
        self.calls = 0

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        if stream:
            raise NotImplementedError("mock client does not stream")
        return self._respond(messages[-1].text)

    async def _respond(self, prompt):
        self.calls += 1
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if self.mode == "slow":
            await asyncio.sleep(30)
        if self.mode == "fail":
            raise RuntimeError("mock model unavailable")
        if self.mode == "bad_json":
            return self._reply("Sure! Here is my answer: the match is exciting.")
        payload = json.loads(prompt)
        out = MOCK_TASKS[payload["task"]](payload)
        extra = {"hallucinate": " This is their 47th straight win at a ground holding 99,999 fans.",
                 "wrong_name": " Lionel Vargas set it up from the left.",
                 "off_topic": " It caps the best season of his career."}.get(self.mode)
        if self.mode == "contradict" and payload.get("facts", {}).get("score"):
            score = payload["facts"]["score"]
            extra = f" {min(score, key=score.get)} lead the game."  # wrong: that team is not ahead
        if extra:
            for key in ("why", "line", "summary"):
                if key in out:
                    out[key] += extra
        return self._reply(json.dumps(out, ensure_ascii=False))

    @staticmethod
    def _reply(text):
        return ChatResponse(messages=[Message("assistant", [text])], model="mock")


def _mock_personalize(p):
    o, prof = p["overlay"], p["profile"]
    out = dict(o)
    if prof["mode"] == "fan":
        out["why"] = o["why"].split(". ")[0].rstrip(".") + "."
    if prof["language_code"] != "en":  # the mock cannot translate; it marks the target language
        out = {k: f"[{prof['language_code']}] {v}" for k, v in out.items()}
    return out


MOCK_TASKS = {
    "explain": lambda p: templates.explain(p["facts"]),
    "commentary": lambda p: {"line": templates.commentary(p["facts"], p["explanation"])},
    "recap": lambda p: templates.recap(p["facts"]),
    "personalize": _mock_personalize,
}
