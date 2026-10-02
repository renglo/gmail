"""JSON Schema documents returned by handler describe()."""

from typing import Any, Dict, List, Optional


def describe_document(
    handler: str,
    title: str,
    description: str,
    properties: Dict[str, Any],
    required: Optional[List[str]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    instructions: Optional[str] = None,
    execution: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    input_schema: Dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        input_schema["required"] = required
    output: Dict[str, Any] = {
        "described": True,
        "handler": handler,
        "title": title,
        "description": description,
        "input_schema": input_schema,
        "output_schema": output_schema or {"type": "object"},
    }
    if instructions:
        output["instructions"] = instructions
    if execution:
        output["execution"] = execution
    return {
        "success": True,
        "action": "describe",
        "output": output,
    }
