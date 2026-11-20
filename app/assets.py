from __future__ import annotations

from enum import Enum
from pathlib import Path

class Banner(str, Enum):
    WELCOME = "welcome"
    PROFILE = "profile"
    ORDER_CHOICE = "order_choice"
    ARTICLE = "article"
    MYSTERY = "mystery"
    PAYMENT = "payment"
    ALMOST = "almost"
    APPROVED = "approved"
    REJECTED = "rejected"

    def path_for(self, cover_id: int) -> Path:
        """Banner file of a specific cover (``<cover>/banners/<banner>.png``)."""
        from app.covers.loader import cover_banner_path

        return cover_banner_path(cover_id, self)
