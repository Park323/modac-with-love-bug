"""
Converts a natural language query into a list of position waypoints
using the Claude API.

Requires:
  - ANTHROPIC_API_KEY environment variable
  - assets/mapinfo.json (optional; if present, used as map context for the LLM)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load_mapinfo(path: str = "assets/mapinfo.json") -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def query_to_waypoints(query: str, mapinfo: dict | None = None) -> list[dict[str, float]]:
    """
    Send a natural language query to Claude and parse the returned waypoint list.

    Returns a list of dicts: [{"x": float, "y": float, "rot": float}, ...]
    rot is facing direction in degrees (0=north, clockwise).
    """
    import anthropic

    map_context = ""
    if mapinfo:
        map_context = f"\n\nMap layout reference:\n{json.dumps(mapinfo, indent=2)}"

    system = (
        "You are a QA automation assistant for CrossFire, TDM mode, Transport Ship 2.0 map. "
        "Given a natural language movement goal, return a JSON array of waypoints the character "
        "must visit in order. Each waypoint: {\"x\": float, \"y\": float, \"rot\": float} where "
        "rot is the facing direction in degrees (0=north, 90=east, 180=south, 270=west, clockwise). "
        "x and y are map coordinates. Return ONLY the raw JSON array with no explanation or markdown."
        + map_context
    )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": query}],
        system=system,
    )

    raw: str = message.content[0].text.strip()

    # strip markdown code fences if the model wraps output
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

    parsed: list[dict[str, Any]] = json.loads(raw)
    return [{"x": float(w["x"]), "y": float(w["y"]), "rot": float(w["rot"])} for w in parsed]
