from __future__ import annotations

import os

import uvicorn


def main() -> None:
    role = os.getenv("TRUSTCART_SERVICE_ROLE", "merchant-api")
    if role == "worker":
        from .worker import main as worker_main

        worker_main()
        return
    app = "trustcart.agent_api:app" if role == "agent-api" else "trustcart.merchant_api:app"
    port = int(os.getenv("PORT", "8001" if role == "agent-api" else "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True)


if __name__ == "__main__":
    main()
