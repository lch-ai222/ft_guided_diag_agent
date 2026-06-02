from __future__ import annotations

import json
import os
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ft_diag_agent.settings import Settings

T = TypeVar("T", bound=BaseModel)


class LlmProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_error: str | None = None
        self.last_model: str | None = None
        self.last_payload: dict[str, Any] | None = None
        self.last_raw_content: str | None = None

    @property
    def enabled(self) -> bool:
        return self._availability_error() is None

    def json_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        complexity: str = "fast",
        max_tokens: int = 1200,
    ) -> T | None:
        self.last_error = None
        self.last_payload = None
        self.last_raw_content = None
        availability_error = self._availability_error()
        if availability_error:
            self.last_error = availability_error
            return None
        try:
            payload = self._chat_json(system_prompt, user_prompt, complexity, max_tokens)
            self.last_payload = payload
            return response_model.model_validate(payload)
        except ValidationError as exc:
            self.last_error = f"LLM JSON 结构校验失败：{_compact_error(str(exc))}"
            return None
        except Exception as exc:
            self.last_error = f"LLM 调用失败：{_compact_error(_exception_label(exc))}"
            return None

    def _availability_error(self) -> str | None:
        if not self.settings.llm_enable:
            return "LLM_ENABLE 当前为 false，已跳过 LLM 抽取。"
        if self.settings.llm_provider == "deepseek":
            if not os.getenv("DEEPSEEK_API_KEY"):
                return "DEEPSEEK_API_KEY 未配置，无法调用 DeepSeek。"
            return None
        if self.settings.llm_provider == "openai":
            if not os.getenv("OPENAI_API_KEY"):
                return "OPENAI_API_KEY 未配置，无法调用 OpenAI。"
            return None
        return f"不支持的 LLM provider：{self.settings.llm_provider}"

    def _chat_json(self, system_prompt: str, user_prompt: str, complexity: str, max_tokens: int) -> dict[str, Any]:
        if self.settings.llm_provider == "deepseek":
            return self._deepseek_json(system_prompt, user_prompt, complexity, max_tokens)
        if self.settings.llm_provider == "openai":
            return self._openai_json(system_prompt, user_prompt, max_tokens)
        raise ValueError(f"Unsupported LLM provider: {self.settings.llm_provider}")

    def _deepseek_json(
        self,
        system_prompt: str,
        user_prompt: str,
        complexity: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        model = self.settings.deepseek_model_pro if complexity == "pro" else self.settings.deepseek_model_fast
        attempts = [(model, complexity == "pro", "primary")]
        if complexity == "pro":
            attempts.extend(
                [
                    (self.settings.deepseek_model_pro, False, "pro_without_thinking"),
                    (self.settings.deepseek_model_fast, False, "flash_fallback"),
                ]
            )
        errors: list[str] = []
        for attempt_model, use_thinking, label in attempts:
            self.last_model = attempt_model
            try:
                return self._deepseek_json_once(
                    system_prompt,
                    user_prompt,
                    attempt_model,
                    max_tokens,
                    use_thinking=use_thinking,
                )
            except Exception as exc:
                errors.append(f"{label}/{attempt_model}: {_compact_error(_exception_label(exc), 220)}")
        raise RuntimeError("DeepSeek JSON 调用多次重试仍失败：" + " | ".join(errors))

    def _deepseek_json_once(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int,
        *,
        use_thinking: bool,
    ) -> dict[str, Any]:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=self.settings.deepseek_base_url,
        )
        extra_body: dict[str, Any] = {}
        kwargs: dict[str, Any] = {}
        if use_thinking:
            kwargs["reasoning_effort"] = "high"
            extra_body["thinking"] = {"type": "enabled"}
        if extra_body:
            kwargs["extra_body"] = extra_body
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": f"{system_prompt}\nYou must output valid json only.",
                },
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            **kwargs,
        )
        content = response.choices[0].message.content or "{}"
        self.last_raw_content = content
        return json.loads(content)

    def _openai_json(self, system_prompt: str, user_prompt: str, max_tokens: int) -> dict[str, Any]:
        from openai import OpenAI

        self.last_model = self.settings.openai_model
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        response = client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[
                {"role": "system", "content": f"{system_prompt}\nOutput valid json only."},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or "{}"
        self.last_raw_content = content
        return json.loads(content)


def _exception_label(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _compact_error(value: str, limit: int = 500) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
