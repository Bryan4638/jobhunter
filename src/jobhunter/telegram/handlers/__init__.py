from __future__ import annotations

from jobhunter.context import AppContext
from jobhunter.telegram.handlers import (
    commands,
    cv,
    groups,
    menu,
    offers,
    salary,
    sending,
    text,
    thresholds,
)


def register_all(ctx: AppContext) -> None:
    commands.register(ctx)
    groups.register(ctx)
    salary.register(ctx)
    thresholds.register(ctx)
    cv.register(ctx)
    offers.register(ctx)
    sending.register(ctx)
    menu.register(ctx)
    text.register(ctx)
