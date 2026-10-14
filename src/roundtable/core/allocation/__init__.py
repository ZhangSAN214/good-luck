"""分配：按档位/标签抽取、代号、互评分配、乱序、身份遮蔽。"""

from .identity import MASK, IdentityScrubber, identity_terms
from .names import coordinator_name, display_name, member_codes
from .pools import NotEnoughModels, cheapest, draw, eligible, prefer_tags
from .seating import assign_codes, review_assignments, reviews_for, shuffled

__all__ = [
    "MASK",
    "IdentityScrubber",
    "NotEnoughModels",
    "assign_codes",
    "cheapest",
    "coordinator_name",
    "display_name",
    "draw",
    "eligible",
    "identity_terms",
    "member_codes",
    "prefer_tags",
    "review_assignments",
    "reviews_for",
    "shuffled",
]
