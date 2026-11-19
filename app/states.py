from __future__ import annotations

from enum import Enum


class UserState(str, Enum):
    """Conversational mode of a user, persisted in the database.

    Navigation between menus happens through inline callbacks; only states that
    expect free-form text input from the user are tracked here.
    """

    MENU = "menu"
    AWAIT_ARTICLE = "await_article"
    AWAIT_PERSONAL = "await_personal"
    DIALOG = "dialog"
    # Legacy Telegram-admin states, reset to MENU on startup after the panel migration.
    ADMIN_ADD_PRODUCT = "admin_add_product"
    ADMIN_IMPORT_PRODUCTS = "admin_import_products"
    ADMIN_ADD_BOX = "admin_add_box"
    ADMIN_BROADCAST = "admin_broadcast"
