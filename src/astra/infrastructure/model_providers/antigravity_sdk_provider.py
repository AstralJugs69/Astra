"""Antigravity SDK / Google GenAI ModelProvider adapter."""

import asyncio
import json
import os
import time
from typing import Any, Dict, Optional, Tuple, Type, TypeVar
from pydantic import BaseModel
import structlog

from astra.domain.model_ports import CostMetadata

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class AntigravitySdkProvider:
    """Concrete adapter wrapping Google GenAI SDK (google-genai) with Vertex AI and API key support."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
        location: str = "global",
        default_model: str = "gemini-3.7-flash",
        use_vertex_ai: bool = True,
    ):
        self.use_vertex_ai = use_vertex_ai
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.project_id = project_id or os.environ.get("FIRESTORE_PROJECT_ID") or os.environ.get("ASTRA_PROJECT_ID")
        self.location = location
        self.default_model = default_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai
            if self.use_vertex_ai and self.project_id:
                logger.info("using_vertex_ai_client", project_id=self.project_id, location=self.location)
                self._client = genai.Client(vertexai=True, project=self.project_id, location=self.location)
            elif self.api_key:
                self._client = genai.Client(api_key=self.api_key)
            elif self.project_id:
                logger.info("using_vertex_ai_client", project_id=self.project_id, location=self.location)
                self._client = genai.Client(vertexai=True, project=self.project_id, location=self.location)
            else:
                raise ValueError("Neither GCP project_id nor GEMINI_API_KEY configured for model provider")
        return self._client

    async def generate_structured(
        self,
        prompt: str,
        response_schema: Type[T],
        model_name: Optional[str] = None,
        timeout_seconds: float = 5.0,
        system_instruction: Optional[str] = None,
        tier: str = "fast",
    ) -> Tuple[T, CostMetadata]:
        """Generates structured output parsed into Pydantic model schema."""
        model = model_name or self.default_model
        start_time = time.perf_counter()

        client = self._get_client()
        from google.genai import types

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            system_instruction=system_instruction,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        try:
            async with asyncio.timeout(timeout_seconds):
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )

            latency_ms = int((time.perf_counter() - start_time) * 1000)

            raw_text = response.text or "{}"
            parsed_data = json.loads(raw_text)
            parsed_obj = response_schema.model_validate(parsed_data)

            tokens_in = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            tokens_out = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

            cost = CostMetadata(
                tier_invoked=tier,
                model_name=model,
                model_calls=1,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
            )
            return parsed_obj, cost

        except asyncio.TimeoutError:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            logger.warning("model_call_timed_out", model=model, timeout_seconds=timeout_seconds, latency_ms=latency_ms)
            raise
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            logger.error("model_call_failed", model=model, error=str(exc), latency_ms=latency_ms)
            raise

    async def generate_text(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        timeout_seconds: float = 5.0,
        system_instruction: Optional[str] = None,
        tier: str = "fast",
    ) -> Tuple[str, CostMetadata]:
        """Generates raw text response."""
        model = model_name or self.default_model
        start_time = time.perf_counter()

        client = self._get_client()
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        try:
            async with asyncio.timeout(timeout_seconds):
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )

            latency_ms = int((time.perf_counter() - start_time) * 1000)
            text_out = response.text or ""

            tokens_in = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            tokens_out = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

            cost = CostMetadata(
                tier_invoked=tier,
                model_name=model,
                model_calls=1,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
            )
            return text_out, cost

        except asyncio.TimeoutError:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            logger.warning("model_call_timed_out", model=model, timeout_seconds=timeout_seconds, latency_ms=latency_ms)
            raise
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            logger.error("model_call_failed", model=model, error=str(exc), latency_ms=latency_ms)
            raise
