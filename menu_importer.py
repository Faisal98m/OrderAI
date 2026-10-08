import re
from typing import Optional

from flask import json


def slugify(text: str) -> str:
    """Convert a menu item name into a stable ID."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def clean_text(text: str) -> str:
    """Clean HTML entities / formatting artefacts we may get from websites."""
    return (
        text.replace("&#xA;", " ")
        .replace("&amp;", "&")
        .strip()
    )


def import_menu_text(
    raw_text: str,
    restaurant_name: str,
    source_type: str = "website",
    source_url: Optional[str] = None,
) -> dict:

    menu = {
        "restaurant": restaurant_name,
        "currency": "GBP",
        "currency_symbol": "£",
        "source": {
            "type": source_type,
            "url": source_url,
        },
        "import_status": "needs_review",
        "categories": [],
    }

    lines = [
        clean_text(line)
        for line in raw_text.splitlines()
        if clean_text(line)
    ]

    # For this first importer test these are detected from the source.
    # Later, URL/PDF extraction will supply richer structure.
    category_names = {
        "LOADED BOXES",
        "SINGLE CHICKEN BURGERS",
        "MAINS",
        "APPETISERS",
        "DRINKS",
    }

    known_tags = {
        "SPICY",
    }

    current_category = None
    pending_tags = []
    pending_title_parts = []

    i = 0

    while i < len(lines):
        line = lines[i]

        # -------------------------
        # CATEGORY
        # -------------------------
        if line.upper() in category_names:

            current_category = {
                "name": line.title(),
                "description": None,
                "items": [],
            }

            menu["categories"].append(current_category)

            pending_tags = []
            pending_title_parts = []

            # The next line is the category tagline
            if i + 1 < len(lines):
                next_line = lines[i + 1]

                if (
                    next_line.upper() not in category_names
                    and next_line.upper() not in known_tags
                ):
                    current_category["description"] = next_line
                    i += 1

            i += 1
            continue

        if current_category is None:
            i += 1
            continue

        # -------------------------
        # TAG
        # -------------------------
        if line.upper() in known_tags:
            pending_tags.append(line.lower())
            i += 1
            continue

        # -------------------------
        # WORK OUT WHETHER THIS IS
        # TITLE OR DESCRIPTION
        # -------------------------

        # Descriptions tend to be full sentences / longer text.
        looks_like_description = (
            len(line.split()) >= 5
            or line.endswith(".")
        )

        if looks_like_description and pending_title_parts:

            item_name = " ".join(pending_title_parts)

            item = {
                "id": slugify(item_name),
                "name": item_name.title(),
                "price": None,
                "description": line,
                "tags": pending_tags.copy(),
                "status": "needs_review",
                "source_text": line,
            }

            current_category["items"].append(item)

            pending_title_parts = []
            pending_tags = []

        else:
            # Treat short lines as possible title components.
            pending_title_parts.append(line)

        i += 1

    # -------------------------
    # HANDLE LAST ITEM
    # e.g. drinks with no descriptions
    # -------------------------

    if pending_title_parts and current_category:
        for title in pending_title_parts:
            current_category["items"].append({
                "id": slugify(title),
                "name": title.title(),
                "price": None,
                "description": "",
                "tags": [],
                "status": "needs_review",
                "source_text": title,
            })
            

    return menu


def validate_imported_menu(menu: dict) -> dict:
    blocking_issues = []
    warnings = []

    item_count = 0
    missing_prices = 0
    missing_descriptions = 0

    for category in menu.get("categories", []):
        for item in category.get("items", []):
            item_count += 1

            # Price is required before the menu can go live
            if item.get("price") is None:
                missing_prices += 1

                blocking_issues.append({
                    "type": "missing_price",
                    "category": category["name"],
                    "item": item["name"],
                    "item_id": item["id"],
                })

            # Description is useful, but not required
            if not item.get("description"):
                missing_descriptions += 1

                warnings.append({
                    "type": "missing_description",
                    "category": category["name"],
                    "item": item["name"],
                    "item_id": item["id"],
                })

    # An importer returning zero items is always a failure
    if item_count == 0:
        blocking_issues.append({
            "type": "no_items_found",
            "message": "No menu items were extracted."
        })

    return {
        "ready_to_publish": len(blocking_issues) == 0,
        "item_count": item_count,
        "missing_prices": missing_prices,
        "missing_descriptions": missing_descriptions,
        "blocking_issue_count": len(blocking_issues),
        "warning_count": len(warnings),
        "blocking_issues": blocking_issues,
        "warnings": warnings,
    }
    
def update_item_price(menu: dict, item_id: str, price: float) -> bool:
    """
    Update the price of a menu item by item ID.

    Returns True if the item was found and updated.
    Returns False if no matching item was found.
    """

    for category in menu.get("categories", []):
        for item in category.get("items", []):

            if item.get("id") == item_id:
                item["price"] = float(price)

                # Item can still remain under review for other reasons,
                # but the missing-price issue has now been resolved.
                return True

    return False

def review_missing_prices(menu: dict) -> dict:
    """
    Ask the reviewer to enter prices for any items
    that are missing them.
    """

    for category in menu.get("categories", []):
        print(f"\n--- {category['name']} ---")

        for item in category.get("items", []):

            if item.get("price") is not None:
                continue

            while True:
                value = input(
                    f"{item['name']} - enter price "
                    f"(or press Enter to leave unresolved): £"
                ).strip()

                if value == "":
                    print("Skipped.")
                    break

                try:
                    price = float(value)

                    if price < 0:
                        print("Price cannot be negative.")
                        continue

                    item["price"] = round(price, 2)
                    print(f"Saved: £{item['price']:.2f}")
                    break

                except ValueError:
                    print("Please enter a valid number, e.g. 6.99")

    return menu


def save_menu(menu: dict, filepath: str) -> None:
    with open(filepath, "w", encoding="utf-8") as file:
        json.dump(menu, file, indent=2, ensure_ascii=False)