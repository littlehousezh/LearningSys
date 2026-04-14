from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - depends on local environment
    requests = None


ROLE_ENV_SUFFIXES = {
    "planner": "PLANNER",
    "coder": "CODER",
    "reviewer": "REVIEWER",
    "tester": "TESTER",
    "support": "SUPPORT",
}

ROLE_ALIASES = {
    "Task Planner": "planner",
    "Patch Author": "coder",
    "Code Reviewer": "reviewer",
    "Test Runner": "tester",
}


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


@dataclass(frozen=True)
class VanderbiltConfig:
    base_url: str
    bearer_token: str
    model_id: str
    role_model_ids: Dict[str, str]
    timeout_seconds: int = 120
    chat_path: str = "/chat"

    @classmethod
    def from_env(cls) -> "VanderbiltConfig":
        role_model_ids = {}
        for role, suffix in ROLE_ENV_SUFFIXES.items():
            role_model_ids[role] = _env_first(
                f"AMPLIFY_MODEL_ID_{suffix}",
                f"VANDERBILT_MODEL_ID_{suffix}",
            )

        return cls(
            base_url=_env_first(
                "AMPLIFY_BASE",
                "AMPLIFY_BASE_URL",
                "VANDERBILT_BASE_URL",
                default="https://prod-api.vanderbilt.ai",
            ),
            bearer_token=_env_first("AMPLIFY_BEARER", "VANDERBILT_BEARER"),
            model_id=_env_first(
                "AMPLIFY_MODEL_ID",
                "VANDERBILT_MODEL_ID",
                default="gpt-5",
            ),
            role_model_ids=role_model_ids,
            timeout_seconds=int(
                _env_first(
                    "AMPLIFY_TIMEOUT_SECONDS",
                    "VANDERBILT_TIMEOUT_SECONDS",
                    default="120",
                )
            ),
            chat_path=_env_first(
                "AMPLIFY_CHAT_PATH",
                "VANDERBILT_CHAT_PATH",
                default="/chat",
            ),
        )


class VanderbiltClient:
    def __init__(self, config: VanderbiltConfig) -> None:
        self.config = config

    @classmethod
    def from_env(cls) -> "VanderbiltClient":
        return cls(VanderbiltConfig.from_env())

    def resolve_model_id(self, role: Optional[str] = None) -> str:
        normalized_role = ROLE_ALIASES.get(role or "", role or "")
        return self.config.role_model_ids.get(normalized_role) or self.config.model_id

    def chat_once(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        role: Optional[str] = None,
    ) -> str:
        if requests is None:
            return "The requests package is not installed. Install dependencies before using AI agents."
        if not self.config.bearer_token:
            return "Amplify/Vanderbilt token is not set. Add AMPLIFY_BEARER or VANDERBILT_BEARER in environment variables."

        model_id = self.resolve_model_id(role)
        payload = {
            "data": {
                "temperature": temperature,
                "max_tokens": max_tokens,
                "dataSources": [],
                "messages": [{"role": "user", "content": prompt}],
                "options": {
                    "ragOnly": False,
                    "skipRag": True,
                    "model": {"id": model_id},
                    "prompt": prompt,
                },
            }
        }

        try:
            response = requests.post(
                f"{self.config.base_url.rstrip('/')}{self._chat_path()}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.config.bearer_token}",
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/json",
                },
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            return f"Vanderbilt API request failed: {exc}"

        if not 200 <= response.status_code < 300:
            preview = response.text[:2000]
            return f"Vanderbilt API error {response.status_code}: {preview}"

        try:
            data = response.json()
        except Exception:
            return response.text

        parsed = self._extract_text(data)
        return parsed if parsed else response.text

    def _chat_path(self) -> str:
        path = self.config.chat_path
        return path if path.startswith("/") else f"/{path}"

    def _extract_text(self, payload: Dict[str, Any]) -> Optional[str]:
        data = payload.get("data")
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            content = data.get("content")
            if isinstance(content, str):
                return content
            output = data.get("output")
            if isinstance(output, str):
                return output

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                chunks = [
                    item.get("text")
                    for item in content
                    if isinstance(item, dict) and isinstance(item.get("text"), str)
                ]
                if chunks:
                    return "\n".join(chunks)

        content = payload.get("content")
        if isinstance(content, str):
            return content

        return None
