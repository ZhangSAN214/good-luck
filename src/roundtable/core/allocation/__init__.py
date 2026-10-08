"""分配：按档位/标签抽取、代号、互评分配、乱序、身份遮蔽。"""

from .identity import MASK, IdentityScrubber, identity_terms
from .pools import NotEnoughModels, cheapest, draw, eligible, prefer_tags
from .seating import assign_codes, review_assignments, reviews_for, shuffled

__all__ = [
    "MASK",
    "IdentityScrubber",
    "NotEnoughModels",
    "assign_codes",
    "cheapest",
    "draw",
    "eligible",
    "identity_terms",
    "prefer_tags",
    "review_assignments",
    "reviews_for",
    "shuffled",
]
