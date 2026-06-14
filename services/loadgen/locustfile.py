import random
import uuid

from locust import HttpUser, between, task


MENU_ITEMS = [
    ("Burger", 9.99),
    ("Pizza", 13.99),
    ("Chicken Wrap", 11.99),
    ("Fries", 3.99),
    ("Salad", 8.99),
    ("Noodles", 10.99),
    ("Rice Bowl", 12.50),
    ("Tacos", 9.50),
]


class CustomerUser(HttpUser):
    wait_time = between(0.2, 2.0)

    @task
    def place_order(self):
        client_order_id = f"load-{uuid.uuid4()}"

        items = []

        for _ in range(random.randint(1, 3)):
            name, price = random.choice(MENU_ITEMS)
            items.append(
                {
                    "name": name,
                    "quantity": random.randint(1, 2),
                    "price": price,
                }
            )

        payload = {
            "client_order_id": client_order_id,
            "customer_id": f"customer-{random.randint(1, 5000)}",
            "restaurant_id": f"restaurant-{random.randint(1, 50)}",
            "items": items,
            "delivery_address": {
                "line1": f"{random.randint(1, 999)} Demo Street",
                "city": "Demo City",
                "postcode": "00000",
            },
        }

        self.client.post(
            "/orders",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": client_order_id,
            },
            json=payload,
            name="POST /orders",
        )
