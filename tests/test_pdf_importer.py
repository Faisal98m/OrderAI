from pathlib import Path

from orderai.menu_ingestion.menu_importer import extract_pdf_blocks


def main():
    PDF_PATH = "tests/fixtures/burger_and_sauce_menu.pdf"

    OUTPUT_PATH = Path(
        "tests/output/burger_and_sauce_pdf_blocks.txt"
    )


    text = extract_pdf_blocks(PDF_PATH)


    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_PATH.write_text(
        text,
        encoding="utf-8",
    )


    print("PDF block extraction complete.")
    print(f"Saved output to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
