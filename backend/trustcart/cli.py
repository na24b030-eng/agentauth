from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import get_settings
from .crypto import generate_p256_private_key, private_key_to_pem, public_key_to_pem
from .db import SessionLocal
from .seed import seed_demo


def init_secrets(path: Path) -> None:
    if path.exists():
        raise SystemExit(f"Refusing to overwrite {path}")
    auth_key = generate_p256_private_key()
    agent_key = generate_p256_private_key()
    lines = [
        "# Generated development secrets. Never commit this file.",
        "TRUSTCART_DEMO_AUTH_PRIVATE_KEY_PEM=" + json.dumps(private_key_to_pem(auth_key)),
        "TRUSTCART_DEMO_AUTH_PUBLIC_KEY_PEM="
        + json.dumps(public_key_to_pem(auth_key.public_key())),
        "TRUSTCART_AGENT_PRIVATE_KEY_PEM=" + json.dumps(private_key_to_pem(agent_key)),
        "TRUSTCART_DEMO_PASSCODE=trustcart-demo",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated independent demo-auth and agent keys in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="trustcart")
    sub = parser.add_subparsers(dest="command", required=True)
    secrets_parser = sub.add_parser("init-secrets")
    secrets_parser.add_argument("--output", type=Path, default=Path(".env"))
    sub.add_parser("seed")
    args = parser.parse_args()
    if args.command == "init-secrets":
        init_secrets(args.output)
    elif args.command == "seed":
        with SessionLocal.begin() as session:
            print(json.dumps(seed_demo(session, get_settings()), indent=2))


if __name__ == "__main__":
    main()
