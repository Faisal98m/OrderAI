import json
from pathlib import Path
from orderai.paths import REPOSITORY_ROOT


BASE_DIR = REPOSITORY_ROOT
RESTAURANTS_DIR = BASE_DIR / "restaurants"


def load_restaurant_config(restaurant_id):
    config_path = RESTAURANTS_DIR / restaurant_id / "config.json"

    with open(config_path, "r", encoding="utf-8") as file:
        return json.load(file)


def load_menu(restaurant_id):
    menu_path = RESTAURANTS_DIR / restaurant_id / "menu.json"

    with open(menu_path, "r", encoding="utf-8") as file:
        return json.load(file)