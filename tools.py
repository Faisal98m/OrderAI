import json

order = {
    "items": []
}


def load_menu():
    with open("menu.json", "r") as file:
        menu = json.load(file)

    return menu


def search_menu(search_term):
    menu = load_menu()
    results = []

    search_term = search_term.lower().strip()

    # Normalise simple plural searches
    if search_term.endswith("s"):
        singular_term = search_term[:-1]
    else:
        singular_term = search_term

    for category_name, items in menu["categories"].items():

        category_matches = (
            search_term == category_name.lower()
            or singular_term == category_name.lower().rstrip("s")
        )

        for item in items:
            aliases = " ".join(item.get("aliases", []))

            searchable_text = (
                item["name"] + " "
                + item["description"] + " "
                + aliases
            ).lower()

            item_matches = (
                search_term in searchable_text
                or singular_term in searchable_text
            )

            if category_matches or item_matches:
                results.append({
                    "id": item["id"],
                    "name": item["name"],
                    "price": item["price"],
                    "currency": menu["currency"],
                    "currency_symbol": menu["currency_symbol"],
                    "description": item["description"],
                    "category": category_name
                })

    return results


def resolve_menu_item(search_term):
    """
    Resolve a customer's description against the menu.

    Returns:
    - not_found if there are no matches
    - resolved if exactly one item matches
    - ambiguous if multiple items match
    """

    matches = search_menu(search_term)

    if len(matches) == 0:
        return {
            "status": "not_found",
            "matches": []
        }

    if len(matches) == 1:
        return {
            "status": "resolved",
            "item": matches[0]
        }

    return {
        "status": "ambiguous",
        "matches": matches
    }
    
def add_item_to_order(search_term, quantity=1):
    """
    Safely resolve a customer's menu description before
    changing the order.
    """

    resolution = resolve_menu_item(search_term)

    if resolution["status"] == "not_found":
        return {
            "status": "not_found",
            "message": "No matching menu item was found."
        }

    if resolution["status"] == "ambiguous":
        return {
            "status": "ambiguous",
            "matches": resolution["matches"]
        }

    item = resolution["item"]

    updated_order = add_to_order(
        item["id"],
        quantity
    )

    return {
        "status": "added",
        "item": item,
        "order": updated_order
    }

def add_to_order(item_id, quantity=1):
    menu = load_menu()

    for category_name, items in menu["categories"].items():
        for item in items:

            if item["id"] == item_id:

                for order_item in order["items"]:
                    if order_item["id"] == item_id:
                        order_item["quantity"] += quantity
                        return order

                order["items"].append({
                    "id": item["id"],
                    "name": item["name"],
                    "price": item["price"],
                    "quantity": quantity
                })

                return order

    return {
        "error": "Item not found"
    }


def get_order():
    total = sum(
        item["price"] * item["quantity"]
        for item in order["items"]
    )

    return {
        "items": order["items"],
        "total": round(total, 2),
        "currency": "GBP",
        "currency_symbol": "£"
    }


def remove_from_order(item_id, quantity=1):

    for item in order["items"]:

        if item["id"] == item_id:

            item["quantity"] -= quantity

            if item["quantity"] <= 0:
                order["items"].remove(item)

            return get_order()

    return {
        "error": "Item not found in order"
    }