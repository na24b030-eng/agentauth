from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from agents import Runner, set_default_openai_key
from trustcart.agent_runtime import CommerceRunContext, build_agent
from trustcart.crypto import generate_p256_private_key, private_key_to_pem

ROOT = Path(__file__).parent
PRODUCTS = [
    {
        "id": str(uuid.uuid4()),
        "sku": "MILK-1L",
        "name": "Toned Milk",
        "category": "dairy",
        "unit_price_paise": 6400,
        "available_quantity": 40,
        "tags": ["vegetarian"],
        "description": "1 litre",
    },
    {
        "id": str(uuid.uuid4()),
        "sku": "BREAD-WW",
        "name": "Whole Wheat Bread",
        "category": "bakery",
        "unit_price_paise": 5200,
        "available_quantity": 25,
        "tags": ["vegetarian"],
        "description": "whole wheat",
    },
    {
        "id": str(uuid.uuid4()),
        "sku": "EGGS-12",
        "name": "Free Range Eggs",
        "category": "breakfast",
        "unit_price_paise": 11800,
        "available_quantity": 20,
        "tags": ["high-protein"],
        "description": "12 eggs",
    },
    {
        "id": str(uuid.uuid4()),
        "sku": "BANANA-6",
        "name": "Bananas",
        "category": "produce",
        "unit_price_paise": 4800,
        "available_quantity": 30,
        "tags": ["vegan"],
        "description": "six",
    },
    {
        "id": str(uuid.uuid4()),
        "sku": "RICE-5K",
        "name": "Basmati Rice",
        "category": "staples",
        "unit_price_paise": 44900,
        "available_quantity": 15,
        "tags": ["vegan"],
        "description": "5 kg",
    },
    {
        "id": str(uuid.uuid4()),
        "sku": "DAL-1K",
        "name": "Toor Dal",
        "category": "staples",
        "unit_price_paise": 18900,
        "available_quantity": 20,
        "tags": ["vegan", "high-protein"],
        "description": "1 kg",
    },
]
PRICES = {item["sku"]: item["unit_price_paise"] for item in PRODUCTS}


def fake_merchant(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/catalog/search"):
        return httpx.Response(200, json=PRODUCTS)
    if path.endswith("/usual-basket"):
        return httpx.Response(
            200,
            json={
                "items": [
                    {"sku": "MILK-1L", "quantity": 2},
                    {"sku": "BREAD-WW", "quantity": 1},
                    {"sku": "EGGS-12", "quantity": 1},
                    {"sku": "BANANA-6", "quantity": 1},
                ]
            },
        )
    if path.endswith("/delivery-options"):
        return httpx.Response(
            200,
            json=[
                {
                    "id": "tonight",
                    "label": "Tonight",
                    "window": "18:00–21:00",
                    "fee_paise": 4900,
                    "cutoff_at": "2026-08-25T17:00:00Z",
                },
                {
                    "id": "express",
                    "label": "Express",
                    "window": "45–60 minutes",
                    "fee_paise": 7900,
                    "cutoff_at": "2026-08-25T23:00:00Z",
                },
                {
                    "id": "tomorrow",
                    "label": "Tomorrow",
                    "window": "08:00–11:00",
                    "fee_paise": 2900,
                    "cutoff_at": "2026-08-25T23:00:00Z",
                },
            ],
        )
    if path.endswith("/quotes"):
        body = json.loads(request.content)
        subtotal = sum(PRICES.get(item["sku"], 999999) * item["quantity"] for item in body["items"])
        fee = {"tonight": 4900, "express": 7900, "tomorrow": 2900}.get(
            body["delivery_option_id"], 4900
        )
        return httpx.Response(
            200,
            json={
                "id": str(uuid.uuid4()),
                "status": "OPEN",
                "total_paise": subtotal + fee,
                "expires_at": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
                "items": body["items"],
            },
        )
    if path.endswith("/checkouts"):
        return httpx.Response(
            200, json={"id": str(uuid.uuid4()), "status": "CANCEL_WINDOW", "amount_paise": 84200}
        )
    return httpx.Response(404, json={"code": "NOT_FOUND", "message": path})


def outcome(ctx: CommerceRunContext, final: str) -> str:
    if ctx.checkout_created:
        return "execute"
    if ctx.approval_requested or ctx.active_quote:
        return "proposal"
    if ctx.tool_calls == 0 and ("?" in final or "clarif" in final.lower()):
        return "clarify"
    return "browse"


async def evaluate(effort: str, scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    key = private_key_to_pem(generate_p256_private_key())
    for scenario in scenarios:
        ctx = CommerceRunContext(
            run_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            grant_id=uuid.uuid4(),
            grant_digest="d" * 64,
            auto_execute=scenario["auto_execute"],
            payment_mode="DELEGATED_DEBIT_SIMULATOR",
            transport=httpx.MockTransport(fake_merchant),
            signing_key_pem=key,
            persist_events=False,
        )
        started = time.perf_counter()
        result = await Runner.run(
            build_agent(effort), scenario["message"], context=ctx, max_turns=8
        )
        latency = round(time.perf_counter() - started, 3)
        actual = outcome(ctx, str(result.final_output))
        expected = scenario["expected"]
        passed = actual == expected or (expected == "no_execute" and actual != "execute")
        usage = result.context_wrapper.usage
        rows.append(
            {
                "id": scenario["id"],
                "effort": effort,
                "expected": expected,
                "actual": actual,
                "passed": passed,
                "tool_calls": ctx.tool_calls,
                "latency_seconds": latency,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "final": str(result.final_output),
            }
        )
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "report.json")
    args = parser.parse_args()
    api_key = os.getenv("TRUSTCART_OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("TRUSTCART_OPENAI_API_KEY is required; no synthetic report was written")
    set_default_openai_key(api_key)
    scenarios = json.loads((ROOT / "scenarios.json").read_text(encoding="utf-8"))
    rows = await evaluate("low", scenarios) + await evaluate("medium", scenarios)
    summary = {
        effort: {
            "completion_rate": sum(row["passed"] for row in rows if row["effort"] == effort)
            / len(scenarios),
            "average_tool_calls": sum(row["tool_calls"] for row in rows if row["effort"] == effort)
            / len(scenarios),
            "average_latency_seconds": sum(
                row["latency_seconds"] for row in rows if row["effort"] == effort
            )
            / len(scenarios),
            "total_tokens": sum(
                row["input_tokens"] + row["output_tokens"]
                for row in rows
                if row["effort"] == effort
            ),
        }
        for effort in ("low", "medium")
    }
    args.output.write_text(
        json.dumps({"summary": summary, "results": rows}, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
