"""Coerce an agent's output into a Pydantic model.

Agno returns a parsed model when structured-output parsing fires, but with some
model/tool combinations the output comes back as a dict or a JSON string. This
normalizes all three so callers always get a typed object.
"""
from __future__ import annotations

import re
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def coerce_output(content, model_cls: Type[T]) -> T:
    if isinstance(content, model_cls):
        return content
    if isinstance(content, dict):
        return model_cls.model_validate(content)
    text = str(content).strip()
    # drop any <reasoning>/<think> blocks some models emit before the answer
    text = re.sub(r"<(reasoning|think)>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = text.strip()
    # strip ```json ... ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        return model_cls.model_validate_json(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)  # find the JSON object in the text
        if match:
            return model_cls.model_validate_json(match.group(0))
        raise
