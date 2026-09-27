import json

order = {
    "items": []
}

pending_action = {
    "action": None,
    "quantity": None,
    "search_term": None
}



def get_pending_action():
    return pending_action


def set_pending_action(action, quantity, search_term):
    pending_action["action"] = action
    pending_action["quantity"] = quantity
    pending_action["search_term"] = search_term

    return pending_action


def clear_pending_action():
    pending_action["action"] = None
    pending_action["quantity"] = None
    pending_action["search_term"] = None

    return pending_action


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

     set_pending_action(
        action="add",
        quantity=quantity,
        search_term=search_term
    )

     return {
        "status": "ambiguous",
        "matches": resolution["matches"],
        "pending_action": get_pending_action()
    }
    item = resolution["item"]

    updated_order = add_to_order(
        item["id"],
        quantity
    )

    clear_pending_action()

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
                    "quantity": quantity,
                    "modifiers": []
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
    
    
def continue_pending_action(clarification):
    """
    Continue an unfinished action using the customer's clarification.
    """

    if pending_action["action"] is None:
        return {
            "status": "no_pending_action"
        }

    action = pending_action["action"]
    quantity = pending_action["quantity"]
    original_search = pending_action["search_term"]

    combined_search = f"{clarification} {original_search}"

    if action == "add":
        return add_item_to_order(
            combined_search,
            quantity
        )

    return {
        "status": "unsupported_action"
    }
    
    
def remove_ingredient_from_order_item(item_id, ingredient):
    menu = load_menu()

    ingredient = ingredient.lower().strip()

    # Find the menu item
    menu_item = None

    for category_name, items in menu["categories"].items():
        for item in items:
            if item["id"] == item_id:
                menu_item = item
                break

    if menu_item is None:
        return {
            "status": "item_not_found"
        }

    ingredients = [
        item_ingredient.lower()
        for item_ingredient in menu_item.get("ingredients", [])
    ]

    # Validate the requested modification
    if ingredient not in ingredients:
        return {
            "status": "ingredient_not_found",
            "item": menu_item["name"],
            "ingredient": ingredient
        }

    # Find the item in the customer's actual order
    for order_item in order["items"]:
        if order_item["id"] == item_id:

            modifier = {
                "type": "remove",
                "ingredient": ingredient
            }

            if modifier not in order_item["modifiers"]:
                order_item["modifiers"].append(modifier)

            return {
                "status": "modified",
                "item": order_item
            }

    return {
        "status": "item_not_in_order"
    }
    
    

def modify_order_item(search_term, ingredient):
    """
    Safely remove an ingredient from an item in the current order.
    """

    search_term = search_term.lower().strip()
    ingredient = ingredient.lower().strip()

    matches = []

    # Search only the customer's current order
    for order_item in order["items"]:

        searchable_text = order_item["name"].lower()

        if search_term in searchable_text:
            matches.append(order_item)

    # No matching item in the order
    if len(matches) == 0:
        return {
            "status": "item_not_found"
        }

    # More than one possible target
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "matches": matches
        }

    # Exactly one target
    item = matches[0]

    return remove_ingredient_from_order_item(
        item["id"],
        ingredient
    )