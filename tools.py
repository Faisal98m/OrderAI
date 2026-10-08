import json
import uuid
from session_store import get_session_state
from database import save_order
from whatsapp import send_whatsapp_message
from restaurant import load_menu






def get_pending_action(session_id=None):

    state = get_session_state(
        session_id
    )

    return state["pending_action"]


def set_pending_action(
    action,
    quantity,
    search_term,
    session_id=None
):

    state = get_session_state(
        session_id
    )

    pending_action = state[
        "pending_action"
    ]

    pending_action["action"] = action
    pending_action["quantity"] = quantity
    pending_action["search_term"] = search_term

    return pending_action

def clear_pending_action(
    session_id=None
):

    state = get_session_state(
        session_id
    )

    pending_action = state[
        "pending_action"
    ]

    pending_action["action"] = None
    pending_action["quantity"] = None
    pending_action["search_term"] = None

    return pending_action




def ensure_order_editable(
    session_id=None
):

    state = get_session_state(
        session_id
    )

    order = state["order"]

    if order["status"] in [
        "confirmed",
        "submitted"
    ]:

        return {
            "status": "order_locked",
            "order_status": order["status"],
            "message": (
                "This order can no longer "
                "be changed."
            )
        }

    return None

def mark_order_changed(
    session_id=None
):

    state = get_session_state(
        session_id
    )

    order = state["order"]

    if order["status"] != "submitted":
        order["status"] = "building"
        order["validated_turn"] = None

def search_menu(search_term, restaurant_id):
    menu = load_menu(restaurant_id)
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


def resolve_menu_item(search_term, restaurant_id):
    """
    Resolve a customer's description against the menu.

    Returns:
    - not_found if there are no matches
    - resolved if exactly one item matches
    - ambiguous if multiple items match
    """

    matches = search_menu(
        search_term,
        restaurant_id)

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
    
def add_item_to_order(
    search_term,
    quantity=1,
    restaurant_id="sanis",
    session_id=None
):
    """
    Safely resolve a customer's menu
    description before changing the order.
    """

    state = get_session_state(
        session_id
    )

    order = state["order"]

    resolution = resolve_menu_item(
        search_term,
        restaurant_id
    )

    if resolution["status"] == "not_found":
        return {
            "status": "not_found",
            "message": (
                "No matching menu item "
                "was found."
            )
        }

    if order["status"] == "submitted":
        return {
            "status": "new_order_required",
            "message": (
                "The previous order has already "
                "been submitted. Start a new "
                "order first."
            )
        }

    if resolution["status"] == "ambiguous":

        set_pending_action(
            action="add",
            quantity=quantity,
            search_term=search_term,
            session_id=session_id
        )

        return {
            "status": "ambiguous",
            "matches": resolution["matches"],
            "pending_action":
                get_pending_action(
                    session_id
                )
        }

    item = resolution["item"]

    updated_order = add_to_order(
        item["id"],
        quantity,
        restaurant_id,
        session_id
    )

    clear_pending_action(
        session_id
    )

    return {
        "status": "added",
        "item": item,
        "order": updated_order
    }
    
    
def add_to_order(
    item_id,
    quantity=1,
    restaurant_id="sanis",
    session_id=None
):
    """
    Add an item to the customer's
    session-specific order.
    """

    state = get_session_state(
        session_id
    )

    order = state["order"]

    locked = ensure_order_editable(
        session_id
    )

    if locked:
        return locked

    menu = load_menu(
        restaurant_id
    )

    for category_name, items in (
        menu["categories"].items()
    ):

        for item in items:

            if item["id"] == item_id:

                for order_item in (
                    order["items"]
                ):

                    if (
                        order_item["item_id"]
                        == item_id
                    ):

                        order_item[
                            "quantity"
                        ] += quantity

                        mark_order_changed(
                            session_id
                        )

                        return order

                order["items"].append({
                    "line_id": str(
                        uuid.uuid4()
                    ),
                    "item_id": item["id"],
                    "name": item["name"],
                    "price": item["price"],
                    "quantity": quantity,
                    "modifiers": []
                })

                mark_order_changed(
                    session_id
                )

                return order

    return {
        "error": "Item not found"
    }


def get_order(
    session_id=None
):

    state = get_session_state(
        session_id
    )

    order = state["order"]

    total = sum(
        item["price"]
        * item["quantity"]
        for item in order["items"]
    )

    return {
        "items": order["items"],
        "total": round(total, 2),
        "currency": "GBP",
        "currency_symbol": "£",
        "status": order["status"]
    }


def remove_from_order(
    item_id,
    quantity=1,
    session_id=None
):
    state = get_session_state(session_id)
    order = state["order"]

    for item in order["items"]:

        if item["item_id"] == item_id:

            if quantity <= 0:
                return {
                    "status": "invalid_quantity",
                    "message": "Quantity must be greater than zero."
                }

            if quantity > item["quantity"]:
                return {
                    "status": "invalid_quantity",
                    "message": "Cannot remove more items than are currently in the order.",
                    "requested_quantity": quantity,
                    "available_quantity": item["quantity"]
                }

            item["quantity"] -= quantity

            if item["quantity"] == 0:
                order["items"].remove(item)

            mark_order_changed(session_id)

            return get_order(session_id)

    return {
        "status": "item_not_found"
    }
    
    
    
def continue_pending_action(
    clarification,
    restaurant_id="sanis",
    session_id=None
):
    """
    Continue an unfinished action using
    the customer's clarification.
    """

    pending_action = get_pending_action(
        session_id
    )

    if pending_action["action"] is None:
        return {
            "status": "no_pending_action"
        }

    action = pending_action["action"]
    quantity = pending_action["quantity"]

    original_search = pending_action[
        "search_term"
    ]

    combined_search = (
        f"{clarification} "
        f"{original_search}"
    )

    if action == "add":

        return add_item_to_order(
            combined_search,
            quantity,
            restaurant_id,
            session_id
        )

    return {
        "status": "unsupported_action"
    }
    
    
def remove_ingredient_from_order_item(
    line_id,
    ingredient,
    restaurant_id,
    session_id=None
):
    state = get_session_state(session_id)
    order = state["order"]

    menu = load_menu(restaurant_id)

    ingredient = ingredient.lower().strip()

    target_line = None

    for order_item in order["items"]:
        if order_item["line_id"] == line_id:
            target_line = order_item
            break

    if target_line is None:
        return {
            "status": "line_not_found"
        }

    menu_item = None

    for category_name, items in menu["categories"].items():
        for item in items:
            if item["id"] == target_line["item_id"]:
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

    if ingredient not in ingredients:
        return {
            "status": "ingredient_not_found",
            "item": menu_item["name"],
            "ingredient": ingredient
        }

    modifier = {
        "type": "remove",
        "ingredient": ingredient
    }

    if modifier not in target_line["modifiers"]:
        target_line["modifiers"].append(modifier)

    mark_order_changed(session_id)

    return {
        "status": "modified",
        "item": target_line
    }
    

def modify_order_item(
    search_term,
    ingredient,
    restaurant_id="sanis",
    session_id=None
):
    state = get_session_state(session_id)
    order = state["order"]

    search_term = search_term.lower().strip()
    ingredient = ingredient.lower().strip()

    matches = []

    for order_item in order["items"]:

        searchable_text = order_item["name"].lower()

        if search_term in searchable_text:
            matches.append(order_item)

    if len(matches) == 0:
        return {
            "status": "item_not_found"
        }

    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "matches": matches
        }

    item = matches[0]

    return remove_ingredient_from_order_item(
        item["line_id"],
        ingredient,
        restaurant_id,
        session_id
    )
    
def split_order_line(
    line_id,
    split_quantity,
    copy_modifiers=True,
    session_id=None
):
    state = get_session_state(session_id)
    order = state["order"]

    for order_item in order["items"]:

        if order_item["line_id"] == line_id:

            if split_quantity <= 0:
                return {
                    "status": "invalid_quantity"
                }

            if split_quantity >= order_item["quantity"]:
                return {
                    "status": "invalid_quantity"
                }

            order_item["quantity"] -= split_quantity

            new_line = {
                "line_id": str(uuid.uuid4()),
                "item_id": order_item["item_id"],
                "name": order_item["name"],
                "price": order_item["price"],
                "quantity": split_quantity,
                "modifiers": (
                    list(order_item["modifiers"])
                    if copy_modifiers
                    else []
                )
            }

            order["items"].append(new_line)

            mark_order_changed(session_id)

            return {
                "status": "split",
                "original_line": order_item,
                "new_line": new_line
            }

    return {
        "status": "line_not_found"
    }
    
    

def customize_order_item(
    search_term,
    ingredient,
    quantity=None,
    restaurant_id="sanis",
    session_id=None
):
    state = get_session_state(session_id)
    order = state["order"]

    search_term = search_term.lower().strip()
    ingredient = ingredient.lower().strip()

    matches = []

    for order_item in order["items"]:
        if search_term in order_item["name"].lower():
            matches.append(order_item)

    if len(matches) == 0:
        return {
            "status": "item_not_found"
        }

    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "matches": matches
        }

    target_line = matches[0]

    modifier = {
        "type": "remove",
        "ingredient": ingredient
    }

    modifier_already_applied = (
        modifier in target_line["modifiers"]
    )

    menu = load_menu(restaurant_id)

    menu_item = None

    for category_name, items in menu["categories"].items():
        for item in items:
            if item["id"] == target_line["item_id"]:
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

    if ingredient not in ingredients:
        return {
            "status": "ingredient_not_found",
            "item": menu_item["name"],
            "ingredient": ingredient
        }

    if quantity is None:
        return remove_ingredient_from_order_item(
            target_line["line_id"],
            ingredient,
            restaurant_id,
            session_id
        )

    if quantity <= 0 or quantity > target_line["quantity"]:
        return {
            "status": "invalid_quantity"
        }

    if (
        modifier_already_applied
        and quantity < target_line["quantity"]
    ):
        standard_quantity = (
            target_line["quantity"] - quantity
        )

        split_result = split_order_line(
            target_line["line_id"],
            standard_quantity,
            copy_modifiers=False,
            session_id=session_id
        )

        if split_result["status"] != "split":
            return split_result

        return {
            "status": "modified",
            "modified_line": target_line,
            "standard_line": split_result["new_line"],
            "order": get_order(session_id)
        }

    if quantity == target_line["quantity"]:
        return remove_ingredient_from_order_item(
            target_line["line_id"],
            ingredient,
            restaurant_id,
            session_id
        )

    split_result = split_order_line(
        target_line["line_id"],
        quantity,
        session_id=session_id
    )

    if split_result["status"] != "split":
        return split_result

    new_line = split_result["new_line"]

    return remove_ingredient_from_order_item(
        new_line["line_id"],
        ingredient,
        restaurant_id,
        session_id
    )
    




def customize_order_line(
    line_id,
    ingredient,
    quantity=None,
    restaurant_id="sanis",
    session_id=None
):
    state = get_session_state(session_id)
    order = state["order"]

    target_line = None

    for order_item in order["items"]:
        if order_item["line_id"] == line_id:
            target_line = order_item
            break

    if target_line is None:
        return {
            "status": "line_not_found"
        }

    menu = load_menu(restaurant_id)

    menu_item = None

    for category_name, items in menu["categories"].items():
        for item in items:
            if item["id"] == target_line["item_id"]:
                menu_item = item
                break

    if menu_item is None:
        return {
            "status": "item_not_found"
        }

    ingredient = ingredient.lower().strip()

    ingredients = [
        item_ingredient.lower()
        for item_ingredient in menu_item.get("ingredients", [])
    ]

    if ingredient not in ingredients:
        return {
            "status": "ingredient_not_found",
            "item": menu_item["name"],
            "ingredient": ingredient
        }

    if quantity is None:
        quantity = target_line["quantity"]

    if quantity <= 0 or quantity > target_line["quantity"]:
        return {
            "status": "invalid_quantity"
        }

    if quantity == target_line["quantity"]:
        result = remove_ingredient_from_order_item(
            target_line["line_id"],
            ingredient,
            restaurant_id,
            session_id
        )

        return {
            "status": result["status"],
            "order": get_order(session_id)
        }

    split_result = split_order_line(
        target_line["line_id"],
        quantity,
        session_id=session_id
    )

    if split_result["status"] != "split":
        return split_result

    new_line = split_result["new_line"]

    result = remove_ingredient_from_order_item(
        new_line["line_id"],
        ingredient,
        restaurant_id,
        session_id
    )

    return {
        "status": result["status"],
        "order": get_order(session_id)
    }
    

def validate_order(
    turn_id=None,
    session_id=None
):
    state = get_session_state(session_id)
    order = state["order"]

    if len(order["items"]) == 0:
        return {
            "status": "invalid",
            "reason": "empty_order"
        }

    for order_item in order["items"]:
        if order_item["quantity"] <= 0:
            return {
                "status": "invalid",
                "reason": "invalid_quantity",
                "line_id": order_item["line_id"]
            }

    order["status"] = "awaiting_confirmation"

    return {
        "status": "valid",
        "order": get_order(session_id)
    }
    
def start_new_order(
    session_id=None
):
    state = get_session_state(session_id)
    order = state["order"]

    if order["status"] not in [
        "confirmed",
        "submitted"
    ]:
        return {
            "status": "new_order_not_allowed",
            "order_status": order["status"]
        }

    order["items"].clear()
    order["status"] = "building"
    order["validated_turn"] = None

    clear_pending_action(session_id)

    return {
        "status": "new_order_started",
        "order": get_order(session_id)
    }
    
def confirm_order(
    turn_id=None,
    session_id=None
):
    state = get_session_state(session_id)
    order = state["order"]

    if order["status"] != "awaiting_confirmation":
        return {
            "status": "confirmation_not_allowed",
            "order_status": order["status"]
        }

    order["status"] = "confirmed"

    return {
        "status": "confirmed",
        "order": get_order(session_id)
    }
    
def submit_order(
    session_id=None
):
    state = get_session_state(session_id)
    order = state["order"]

    if order["status"] != "confirmed":
        return {
            "status": "submission_not_allowed",
            "order_status": order["status"]
        }

    order["status"] = "submitted"

    current_order = get_order(session_id)

    order_id = save_order(
        current_order
    )

    summary_lines = []

    for item in current_order["items"]:

        line_total = (
            item["price"]
            * item["quantity"]
        )

        summary_lines.append(
            f'{item["quantity"]}x '
            f'{item["name"]} - '
            f'£{line_total:.2f}'
        )

    staff_summary = "\n".join(
        summary_lines
    )

    staff_message = (
        f"NEW ORDER #{order_id}\n\n"
        f"{staff_summary}\n\n"
        f'Total: £{current_order["total"]:.2f}\n'
        "Status: Submitted"
    )

    whatsapp_result = send_whatsapp_message(
        staff_message
    )

    return {
        "status": "submitted",
        "order_id": order_id,
        "order": current_order,
        "staff_message": staff_message,
        "whatsapp": whatsapp_result
    }


if __name__ == "__main__":

    add_to_order("B001", 2)

    print("BEFORE:")
    print(get_order())

    print("\nTRY REMOVE 50:")
    print(remove_from_order("B001", 50))

    print("\nAFTER:")
    print(get_order())