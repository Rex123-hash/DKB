"""General-assistant tools — all key-free.

  * calculate(expr)  : safe arithmetic (local, no eval security hole).
  * get_weather(city): live weather via Open-Meteo (free, NO API key).

Cricket scores are intentionally not here: there is no reliable no-key cricket
API. It can be added later as an opt-in keyed tool.
"""
from __future__ import annotations

import ast
import operator

import requests

# --- safe calculator ---
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


def calculate(expression: str) -> dict:
    """Evaluate a basic arithmetic expression safely."""
    try:
        result = _eval(ast.parse(expression, mode="eval").body)
        return {"expression": expression, "result": result}
    except Exception:
        return {"expression": expression, "error": "could not evaluate"}


# --- weather (Open-Meteo, no key) ---
_WMO = {
    0: "saaf aasman (clear)", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy",
    45: "kohra (fog)", 48: "freezing fog", 51: "halki boondabaandi", 53: "drizzle",
    55: "dense drizzle", 61: "halki baarish", 63: "baarish", 65: "tez baarish",
    71: "halki barfbaari", 73: "snow", 75: "heavy snow", 80: "baarish ki bauchaar",
    81: "rain showers", 82: "violent rain showers", 95: "aandhi-toofan (thunderstorm)",
    96: "thunderstorm with hail", 99: "severe thunderstorm",
}


def get_weather(city: str) -> dict:
    """Current weather for a city via Open-Meteo geocoding + forecast."""
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1},
            timeout=15,
        ).json()
        if not geo.get("results"):
            return {"city": city, "error": "city not found"}
        loc = geo["results"][0]
        fc = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "current": "temperature_2m,weather_code,wind_speed_10m",
            },
            timeout=15,
        ).json()
        cur = fc.get("current", {})
        return {
            "city": loc["name"],
            "temperature_c": cur.get("temperature_2m"),
            "weather": _WMO.get(cur.get("weather_code"), "unknown"),
            "wind_kmh": cur.get("wind_speed_10m"),
        }
    except requests.RequestException as e:
        return {"city": city, "error": f"weather service unavailable: {e}"}
