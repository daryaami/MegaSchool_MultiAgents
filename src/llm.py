import asyncio
import os
from typing import Any, Dict


class LLMClient:
    async def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
        raise NotImplementedError


class GeminiLLMClient(LLMClient):
    def __init__(self, api_key: str, model: str) -> None:
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Gemini SDK not installed") from exc
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._types = types

    async def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
        def _call() -> str:
            prompt = f"{system_prompt}\n\n{user_prompt}"
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=self._types.GenerateContentConfig(temperature=temperature),
            )
            return response.text or ""

        return await asyncio.to_thread(_call)


class MistralLLMClient(LLMClient):
    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._use_sdk = False
        self._client = None
        try:
            from mistralai import Mistral  # type: ignore

            self._client = Mistral(api_key=api_key)
            self._use_sdk = True
        except Exception:
            self._use_sdk = False
        if not self._use_sdk:
            try:
                import requests  # type: ignore
            except Exception as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("requests is required for Mistral API") from exc
            self._requests = requests

    async def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
        def _call() -> str:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            if self._use_sdk and self._client is not None:
                try:
                    response = self._client.chat.complete(
                        model=self._model,
                        messages=messages,
                    )
                    return response.choices[0].message.content
                except Exception as exc:
                    error_msg = str(exc)
                    if "Connection" in error_msg or "timeout" in error_msg.lower():
                        raise ConnectionError(f"Mistral API недоступен: {error_msg}") from exc
                    raise
            url = f"{self._base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            payload: Dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "temperature": temperature,
            }
            try:
                response = self._requests.post(url, headers=headers, json=payload, timeout=30)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except self._requests.exceptions.ConnectionError as exc:
                raise ConnectionError(f"Mistral API недоступен: соединение разорвано") from exc
            except self._requests.exceptions.Timeout as exc:
                raise TimeoutError(f"Mistral API timeout") from exc
            except Exception as exc:
                raise RuntimeError(f"Mistral API ошибка: {exc}") from exc

        return await asyncio.to_thread(_call)


def get_llm_client() -> LLMClient:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("python-dotenv is required to load .env") from exc
    load_dotenv()
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    gemini_key = os.getenv("GEMINI_API_KEY")
    mistral_key = os.getenv("MISTRAL_API_KEY")

    if not provider:
        provider = "mistral" if mistral_key and not gemini_key else "gemini"

    if provider == "mistral":
        if not mistral_key:
            raise RuntimeError("MISTRAL_API_KEY is required.")
        model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
        base_url = os.getenv("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
        return MistralLLMClient(mistral_key, model, base_url)

    api_key = gemini_key
    model = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required.")
    return GeminiLLMClient(api_key, model)
