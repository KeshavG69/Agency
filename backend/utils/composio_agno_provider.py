"""Custom Composio v3 provider for the Agno framework.

Replaces the retired `composio_agno` package. Mirrors the legacy
`composio_agno.toolset.ComposioToolSet._wrap_tool` output so existing
Agent consumers (which expect Agno `Toolkit` objects) keep working.

Copied verbatim from the Kroolo enterprise backend.
"""

from __future__ import annotations

import json
import typing as t
from inspect import Signature

from agno.tools.toolkit import Toolkit

from pydantic import validate_call
from typing_extensions import Protocol

from composio.core.provider import AgenticProvider, AgenticProviderExecuteFn
from composio.types import Tool
from composio.utils.shared import get_signature_format_from_schema_params


class _ToolFunction(Protocol):
    __signature__: Signature
    __annotations__: t.Dict[str, t.Any]
    __doc__: str

    def __call__(self, *args: t.Any, **kwargs: t.Any) -> str: ...


def _strip_empty_strings(params: t.Dict[str, t.Any]) -> t.Dict[str, t.Any]:
    # The wrapped tool's signature carries EVERY Composio param, so
    # `apply_defaults()` injects defaults for params the LLM never set —
    # empty strings, empty lists and dicts. Forwarding these breaks providers.
    # Keep False and 0 — those are meaningful boolean/numeric values.
    def _is_empty(v: t.Any) -> bool:
        return v is None or v == "" or v == [] or v == {}
    return {k: v for k, v in params.items() if not _is_empty(v)}


# Google Calendar event-type-specific property objects (not used by Outlook, kept
# for parity with the source provider). Each is ONLY valid for its matching eventType.
_GCAL_EVENT_TYPE_PROPS = {
    "focusTimeProperties": "focusTime",
    "outOfOfficeProperties": "outOfOffice",
    "workingLocationProperties": "workingLocation",
    "birthdayProperties": "birthday",
}
_GCAL_EVENT_SLUGS = {
    "GOOGLECALENDAR_CREATE_EVENT",
    "GOOGLECALENDAR_UPDATE_EVENT",
    "GOOGLECALENDAR_PATCH_EVENT",
}

_FLAT_INPUT_SLUGS: t.Set[str] = set()


def _inputs_use_flat_properties(parameters: t.Dict[str, t.Any]) -> bool:
    inp = (parameters.get("properties") or {}).get("inputs")
    if not (isinstance(inp, dict) and inp.get("type") == "array"):
        return False
    item_props = (inp.get("items") or {}).get("properties") or {}
    if not item_props or "properties" in item_props:
        return False
    return any(isinstance(v, dict) and v.get("is_property") for v in item_props.values())


def _sanitize_arguments(slug: str, params: t.Dict[str, t.Any]) -> t.Dict[str, t.Any]:
    """Slug-specific argument fixups applied just before execution."""
    if (slug or "").upper() in _GCAL_EVENT_SLUGS:
        event_type = params.get("eventType")
        for prop, required_type in _GCAL_EVENT_TYPE_PROPS.items():
            if prop in params and event_type != required_type:
                params.pop(prop, None)
        if params.get("create_meeting_room") is False:
            params.pop("create_meeting_room", None)

    # Drop zero-valued pagination/filter keys that some schemas inject as defaults.
    for _zero_key in ("categoryId", "category_id", "maxResults", "max_results",
                      "top", "limit", "pageSize", "page_size", "perPage", "per_page"):
        if params.get(_zero_key) in (0, "0"):
            params.pop(_zero_key, None)

    if (slug or "").upper() in _FLAT_INPUT_SLUGS:
        inputs = params.get("inputs")
        if isinstance(inputs, list):
            for item in inputs:
                if isinstance(item, dict) and isinstance(item.get("properties"), dict):
                    nested = item.pop("properties")
                    for k, v in nested.items():
                        item.setdefault(k, v)
    return params


class AgnoProvider(
    AgenticProvider[Toolkit, t.List[Toolkit]],
    name="agno",
):
    """Composio v3 provider that produces Agno `Toolkit` objects."""

    def wrap_tool(
        self,
        tool: Tool,
        execute_tool: AgenticProviderExecuteFn,
    ) -> Toolkit:
        name = tool.slug
        description = tool.description or ""
        parameters: t.Dict[str, t.Any] = tool.input_parameters or {}

        if _inputs_use_flat_properties(parameters):
            _FLAT_INPUT_SLUGS.add((tool.slug or "").upper())

        params = get_signature_format_from_schema_params(
            schema_params=parameters,
            skip_default=self.skip_default,
        )
        sig = Signature(parameters=params)
        annotations: t.Dict[str, t.Any] = {p.name: p.annotation for p in params}
        annotations["return"] = str

        @validate_call
        def function_template(*args: t.Any, **kwargs: t.Any) -> str:
            bound_args = sig.bind(*args, **kwargs)
            bound_args.apply_defaults()
            arguments = _strip_empty_strings(dict(bound_args.arguments))
            arguments = _sanitize_arguments(tool.slug, arguments)
            result = execute_tool(slug=tool.slug, arguments=arguments)
            return json.dumps(result)

        func = t.cast(_ToolFunction, function_template)
        func.__signature__ = sig
        func.__annotations__ = annotations
        func.__setattr__("__name__", name.lower())

        docstring_parts = [description, "\nArgs:"]
        for param_name, param_info in (parameters.get("properties") or {}).items():
            param_type = param_info.get("type", "any")
            param_desc = param_info.get("description", "No description available")
            docstring_parts.append(f"    {param_name} ({param_type}): {param_desc}")
        docstring_parts.append(
            "\nReturns:\n    str: JSON string containing the function execution result"
        )
        func.__doc__ = "\n".join(docstring_parts)

        toolkit = Toolkit(name=name)
        toolkit.register(func)

        return toolkit

    def wrap_tools(
        self,
        tools: t.Sequence[Tool],
        execute_tool: AgenticProviderExecuteFn,
    ) -> t.List[Toolkit]:
        return [self.wrap_tool(tool, execute_tool) for tool in tools]


__all__ = ["AgnoProvider"]
