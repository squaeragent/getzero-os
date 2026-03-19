"""
ZERO OS — JSON Schema definitions for core interfaces.
"""


def json_schemas() -> dict:
    """Return JSON Schema definitions for Observation, Decision, WorldState."""
    return {
        "Observation": {
            "type": "object",
            "required": ["coin", "dimension", "value", "confidence", "source", "timestamp"],
            "properties": {
                "coin": {"type": "string"},
                "dimension": {"type": "string"},
                "value": {"type": "number"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "source": {"type": "string"},
                "timestamp": {"type": "number"},
                "metadata": {"type": "object", "default": {}},
            },
            "additionalProperties": False,
        },
        "Decision": {
            "type": "object",
            "required": [
                "id", "coin", "action", "confidence", "stop_pct",
                "size_pct", "reasoning", "ttl_hours", "exit_conditions",
                "regime", "timestamp",
            ],
            "properties": {
                "id": {"type": "string"},
                "coin": {"type": "string"},
                "action": {"type": "string", "enum": ["LONG", "SHORT", "CLOSE", "WAIT"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "stop_pct": {"type": "number"},
                "size_pct": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "object"},
                "ttl_hours": {"type": "number"},
                "exit_conditions": {"type": "array"},
                "regime": {"type": "string"},
                "timestamp": {"type": "number"},
                "metadata": {"type": "object", "default": {}},
            },
            "additionalProperties": False,
        },
        "WorldState": {
            "type": "object",
            "required": ["observations", "macro", "timestamp"],
            "properties": {
                "observations": {
                    "type": "object",
                    "description": "coin -> dimension -> Observation",
                    "additionalProperties": {
                        "type": "object",
                        "additionalProperties": {"$ref": "#/Observation"},
                    },
                },
                "macro": {"type": "object"},
                "timestamp": {"type": "number"},
            },
            "additionalProperties": False,
        },
    }
