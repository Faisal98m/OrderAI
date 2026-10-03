import sqlite3
from datetime import datetime


DB_PATH = "orderai.db"


def init_db():
    connection = sqlite3.connect(DB_PATH)

    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            total REAL NOT NULL,
            currency TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            item_id TEXT NOT NULL,
            name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            FOREIGN KEY (order_id)
                REFERENCES orders(id)
        )
    """)

    connection.commit()
    connection.close()


def save_order(order_data):
    connection = sqlite3.connect(DB_PATH)

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO orders (
            created_at,
            status,
            total,
            currency
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            datetime.now().isoformat(),
            order_data["status"],
            order_data["total"],
            order_data["currency"]
        )
    )

    order_id = cursor.lastrowid

    for item in order_data["items"]:
        cursor.execute(
            """
            INSERT INTO order_items (
                order_id,
                item_id,
                name,
                quantity,
                price
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                order_id,
                item["item_id"],
                item["name"],
                item["quantity"],
                item["price"]
            )
        )

    connection.commit()
    connection.close()

    return order_id