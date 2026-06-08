"""Tool definitions for the Brain LLM.

Claude tool use spec. brain.py で 1 step ごとに渡す。
"""
from __future__ import annotations

BUTTONS = ["A", "B", "Start", "Select", "Up", "Down", "Left", "Right", "L", "R"]

TOOLS = [
    {
        "name": "press_buttons",
        "description": (
            "Press one or more GBA buttons in sequence. "
            "Each entry is one tap. Use this for ANY game input. "
            "If you want multiple steps without re-thinking (e.g. walk "
            "4 tiles down, advance dialogue), put them all in one call "
            "to save cost. Stops early if game state changes "
            "unexpectedly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sequence": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 16,
                    "items": {
                        "type": "object",
                        "properties": {
                            "button": {
                                "type": "string",
                                "enum": BUTTONS,
                            },
                            "frames": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 300,
                                "description": (
                                    "Hold duration. 15 = tap, 60 = ~1s."
                                ),
                            },
                        },
                        "required": ["button"],
                    },
                },
            },
            "required": ["sequence"],
        },
    },
    {
        "name": "wait",
        "description": (
            "Advance time without input. Use when waiting for animation, "
            "long dialogue, or a screen transition."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "frames": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 600,
                },
            },
            "required": ["frames"],
        },
    },
    {
        "name": "record_observation",
        "description": (
            "Record one short factual note to long-term memory. "
            "Use sparingly. Example notes: 'starter chosen: Mudkip', "
            "'Pokemon Center location: Petalburg'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 300,
                },
            },
            "required": ["note"],
        },
    },
]
