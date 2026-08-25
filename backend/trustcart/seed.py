from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_passcode
from .config import Settings
from .crypto import jwk_thumbprint, load_private_key, public_key_to_jwk
from .models import Inventory, Merchant, Product, RegisteredAgent, User

DEMO_USER_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
DEMO_MERCHANT_ID = uuid.UUID("20000000-0000-4000-8000-000000000001")
DEMO_AGENT_ID = uuid.UUID("30000000-0000-4000-8000-000000000001")

CATALOG = [
    ("MILK-1L", "FarmFresh Toned Milk", "dairy", 6400, ["vegetarian"], 40),
    ("BREAD-WW", "Whole Wheat Bread", "bakery", 5200, ["vegetarian"], 25),
    ("EGGS-12", "Free Range Eggs · 12", "breakfast", 11800, ["high-protein"], 20),
    ("BANANA-6", "Robusta Bananas · 6", "produce", 4800, ["vegan"], 30),
    ("RICE-5K", "Everyday Basmati Rice · 5 kg", "staples", 44900, ["vegan"], 15),
    ("DAL-1K", "Premium Toor Dal · 1 kg", "staples", 18900, ["vegan", "high-protein"], 20),
    ("OATS-1K", "Rolled Oats · 1 kg", "breakfast", 22400, ["vegan"], 18),
    ("CURD-400", "Fresh Curd · 400 g", "dairy", 5800, ["vegetarian"], 35),
]


def seed_demo(session: Session, settings: Settings) -> dict[str, str]:
    merchant = session.get(Merchant, DEMO_MERCHANT_ID)
    if merchant is None:
        merchant = Merchant(id=DEMO_MERCHANT_ID, name="AgentAuth Daily", currency="INR")
        session.add(merchant)
    else:
        merchant.name = "AgentAuth Daily"
    user = session.get(User, DEMO_USER_ID)
    if user is None:
        user = User(
            id=DEMO_USER_ID,
            email="demo@trustcart.local",
            display_name="Aarav",
            passcode_hash=hash_passcode(settings.demo_passcode.get_secret_value()),
            usual_basket=[
                {"sku": "MILK-1L", "quantity": 2},
                {"sku": "BREAD-WW", "quantity": 1},
                {"sku": "EGGS-12", "quantity": 1},
                {"sku": "BANANA-6", "quantity": 1},
            ],
        )
        session.add(user)
    if not settings.agent_private_key_pem:
        raise RuntimeError(
            "TRUSTCART_AGENT_PRIVATE_KEY_PEM is required to seed the registered agent"
        )
    private_key = load_private_key(settings.agent_private_key_pem.get_secret_value())
    public_jwk = public_key_to_jwk(private_key.public_key())
    agent = session.get(RegisteredAgent, DEMO_AGENT_ID)
    if agent is None:
        agent = RegisteredAgent(
            id=DEMO_AGENT_ID,
            merchant_id=DEMO_MERCHANT_ID,
            name="AgentAuth Commerce Agent",
            public_jwk=public_jwk,
            jwk_thumbprint=jwk_thumbprint(public_jwk),
            key_version=1,
        )
        session.add(agent)
    else:
        agent.name = "AgentAuth Commerce Agent"
    for sku, name, category, price, tags, stock in CATALOG:
        product = session.scalar(
            select(Product).where(Product.merchant_id == DEMO_MERCHANT_ID, Product.sku == sku)
        )
        if product is None:
            product = Product(
                merchant_id=DEMO_MERCHANT_ID,
                sku=sku,
                name=name,
                description=f"Seeded demo product: {name}",
                category=category,
                unit_price_paise=price,
                tags=tags,
            )
            session.add(product)
            session.flush()
            session.add(Inventory(product_id=product.id, on_hand_qty=stock))
    session.flush()
    return {
        "user_id": str(user.id),
        "merchant_id": str(merchant.id),
        "agent_id": str(agent.id),
        "agent_fingerprint": agent.jwk_thumbprint,
    }
