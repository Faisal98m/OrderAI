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


def get_all_orders():
    connection = sqlite3.connect(DB_PATH)

    connection.row_factory = sqlite3.Row

    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            created_at,
            status,
            total,
            currency
        FROM orders
        ORDER BY id DESC
    """)

    order_rows = cursor.fetchall()

    orders = []

    for order_row in order_rows:

        cursor.execute(
            """
            SELECT
                item_id,
                name,
                quantity,
                price
            FROM order_items
            WHERE order_id = ?
            """,
            (order_row["id"],)
        )

        item_rows = cursor.fetchall()

        items = [
            {
                "item_id": item["item_id"],
                "name": item["name"],
                "quantity": item["quantity"],
                "price": item["price"]
            }
            for item in item_rows
        ]

        orders.append({
            "id": order_row["id"],
            "created_at": order_row["created_at"],
            "status": order_row["status"],
            "total": order_row["total"],
            "currency": order_row["currency"],
            "items": items
        })

    connection.close()

    return orders


def update_order_status(order_id, status):
    allowed_statuses = {
        "submitted",
        "preparing",
        "ready",
        "completed"
    }

    if status not in allowed_statuses:
        return {
            "status": "invalid_status"
        }

    connection = sqlite3.connect(DB_PATH)

    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE orders
        SET status = ?
        WHERE id = ?
        """,
        (
            status,
            order_id
        )
    )

    connection.commit()

    updated = cursor.rowcount

    connection.close()

    if updated == 0:
        return {
            "status": "order_not_found"
        }

    return {
        "status": "updated",
        "order_id": order_id,
        "new_status": status
    }