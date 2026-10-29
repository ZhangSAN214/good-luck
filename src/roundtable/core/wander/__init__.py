"""成员自己上网（docs/WANDER.md）。"""

from .wander import STEP, WANDER_PREFIX, Decision, Trip, Wanderer, parse_decision, wander_room
from .web import FakeWeb, PlaywrightWeb, Snapshot, Web, WebError, host_allowed, keep_links, tidy

__all__ = [
    "STEP",
    "WANDER_PREFIX",
    "Decision",
    "FakeWeb",
    "PlaywrightWeb",
    "Snapshot",
    "Trip",
    "Wanderer",
    "Web",
    "WebError",
    "host_allowed",
    "keep_links",
    "parse_decision",
    "tidy",
    "wander_room",
]
