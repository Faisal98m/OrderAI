import re
from typing import Optional
import requests
from bs4 import BeautifulSoup

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
    
def extract_text_from_url(url: str) -> str:
    """
    Fetch a webpage and extract the most likely menu section.
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120 Safari/537.36"
        )
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=15,
    )

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Remove obvious page noise
    for element in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "footer",
            "nav",
        ]
    ):
        element.decompose()

    menu_markers = [
        "LOADED BOXES",
        "SINGLE CHICKEN BURGERS",
        "MAINS",
        "APPETISERS",
        "DRINKS",
    ]

    def find_candidates(tags):
        candidates = []

        for container in soup.find_all(tags):
            text = container.get_text(
                separator="\n",
                strip=True,
            )

            upper_text = text.upper()

            marker_count = sum(
                marker in upper_text
                for marker in menu_markers
            )

            # Ignore tiny navigation / button containers
            if marker_count >= 3 and len(text) >= 500:
                candidates.append({
                    "text": text,
                    "length": len(text),
                    "marker_count": marker_count,
                })

        return candidates

    # First prefer semantic content containers
    candidates = find_candidates(
        ["section", "main", "article"]
    )

    # Only fall back to divs if necessary
    if not candidates:
        candidates = find_candidates(["div"])

    if not candidates:
        return soup.get_text(
            separator="\n",
            strip=True,
        )

    # First prefer the container containing the most
    # menu signals, then the smallest of those.
    candidates.sort(
        key=lambda candidate: (
            -candidate["marker_count"],
            candidate["length"],
        )
    )

    return candidates[0]["text"]

def import_menu_from_url(
    url: str,
    restaurant_name: str,
) -> dict:

    raw_text = extract_text_from_url(url)

    return import_menu_text(
        raw_text=raw_text,
        restaurant_name=restaurant_name,
        source_type="website",
        source_url=url,
        stop_markers=[
            "APPETISERS AND DRINKS",
            "HUNGRY NOW?",
            "FIND A LOCATION",
        ],
    )


def import_menu_text(
    raw_text: str,
    restaurant_name: str,
    source_type: str = "website",
    source_url: Optional[str] = None,
    stop_markers: Optional[list[str]] = None,
) -> dict:
    """
    Convert extracted restaurant menu text into OrderAI's standard menu structure.

    Rules:
    - Never guess missing prices.
    - Missing prices remain None.
    - Missing descriptions are allowed.
    - Short consecutive lines can be combined into an item title.
    - Optional stop markers can be used to stop parsing when page/footer
      content begins.
    """

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

    # For this first importer version, these are known menu category labels.
    # Later we can make category detection more dynamic.
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

    stop_markers = {
        marker.upper()
        for marker in (stop_markers or [])
    }

    current_category = None
    pending_tags = []
    pending_title_parts = []

    i = 0

    while i < len(lines):
        line = lines[i]
        upper_line = line.upper()

        # -------------------------
        # STOP MARKER
        # -------------------------
        if upper_line in stop_markers:
            break

        # -------------------------
        # CATEGORY
        # -------------------------
        if upper_line in category_names:
            current_category = {
                "name": line.title(),
                "description": None,
                "items": [],
            }

            menu["categories"].append(current_category)

            pending_tags = []
            pending_title_parts = []

            # The next line is usually the category tagline.
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                next_upper = next_line.upper()

                if (
                    next_upper not in category_names
                    and next_upper not in known_tags
                    and next_upper not in stop_markers
                ):
                    current_category["description"] = next_line
                    i += 1

            i += 1
            continue

        # Ignore anything before the first recognised category
        if current_category is None:
            i += 1
            continue

        # -------------------------
        # TAG
        # Example: SPICY
        # -------------------------
        if upper_line in known_tags:
            pending_tags.append(line.lower())
            i += 1
            continue

        # -------------------------
        # DETECT DESCRIPTION
        # -------------------------
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
            # Short lines are treated as possible item title parts.
            #
            # Example:
            # Burger
            # Loaded Box
            #
            # becomes:
            # Burger Loaded Box
            pending_title_parts.append(line)

        i += 1

    # -------------------------
    # HANDLE FINAL ITEMS
    # -------------------------
    # This mainly handles categories like Drinks,
    # where items may have no descriptions.
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

    # -------------------------
    # REMOVE EMPTY CATEGORIES
    # -------------------------
    # Website navigation can repeat category names without
    # containing any actual products.
    menu["categories"] = [
        category
        for category in menu["categories"]
        if category["items"]
    ]

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
        
        

def inspect_url_structure(url: str) -> None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120 Safari/537.36"
        )
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=15,
    )

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup.find_all(
        ["main", "section", "article", "div"]
    ):
        text = tag.get_text(" ", strip=True)

        if "LOADED BOXES" in text:
            print("\nTAG:", tag.name)
            print("ID:", tag.get("id"))
            print("CLASS:", tag.get("class"))
            print("TEXT PREVIEW:")
            print(text[:1000])
            print("-" * 80)