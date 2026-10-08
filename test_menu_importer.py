import json

from menu_importer import import_menu_text, review_missing_prices, save_menu, update_item_price, validate_imported_menu


with open("tests/fixtures/sanis_menu.txt", "r", encoding="utf-8") as file:
    raw_menu = file.read()
    print("RAW MENU PREVIEW:")
    print(repr(raw_menu[:500]))
    print()


menu = import_menu_text(
    raw_text=raw_menu,
    restaurant_name="Sanis",
    source_type="website",
)

menu = review_missing_prices(menu)
    
validation = validate_imported_menu(menu)


print("\nFINAL VALIDATION")
print(json.dumps(validation, indent=2))

if validation["ready_to_publish"]:
    save_menu(
        menu,
        "restaurants/sanis/menu.json"
    )

    print("Menu published successfully.")
else:
    print(
        f"Menu cannot be published yet. "
        f"{validation['blocking_issue_count']} blocking issues remain."
    )