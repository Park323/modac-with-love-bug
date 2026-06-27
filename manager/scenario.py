import json


class ScenarioReader:
    """로컬 JSON 시나리오 읽기. events[] 반환 (한 원소 = 이벤트 1개)."""

    @staticmethod
    def read(path: str) -> list[dict]:
        with open(path, encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"invalid JSON: {e}") from e

        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list) or len(events) == 0:
            raise ValueError("no events in scenario")
        return events
