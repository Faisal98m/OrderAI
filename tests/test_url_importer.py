from pathlib import Path
import json

from orderai.menu_ingestion.menu_importer import extract_text_from_url, import_menu_from_url, validate_imported_menu


def main():
    URL = "https://www.sanisuk.co.uk/menu"

    OUTPUT_DIR = Path("tests/output")
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


    raw_text = extract_text_from_url(URL)

    (
        OUTPUT_DIR / "sanis_url_extracted.txt"
    ).write_text(
        raw_text,
        encoding="utf-8",
    )


    menu = import_menu_from_url(
        URL,
        restaurant_name="Sanis",
    )

    (
        OUTPUT_DIR / "sanis_url_menu.json"
    ).write_text(
        json.dumps(
            menu,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


    validation = validate_imported_menu(menu)

    (
        OUTPUT_DIR / "sanis_url_validation.json"
    ).write_text(
        json.dumps(
            validation,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


    print("URL import test complete.")
    print(f"Outputs saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
