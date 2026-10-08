import json

from menu_importer import (
    import_menu_from_url,
    validate_imported_menu,
)


SANIS_MENU_URL = "https://www.sanisuk.co.uk/menu"


menu = import_menu_from_url(
    SANIS_MENU_URL,
    restaurant_name="Sanis",
)

print(json.dumps(menu, indent=2))


validation = validate_imported_menu(menu)

print("\nVALIDATION")
print(json.dumps(validation, indent=2))