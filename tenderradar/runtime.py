"""Process entry helpers.

Windows defaults to ProactorEventLoop, which does not implement add_reader(),
so psycopg refuses to run async on it:

    InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in
    async mode.

Every entry point therefore starts its loop through run() below. This only
matters on the Windows dev machine -- the VPS is Linux and takes the default
path -- but without it nothing that touches the database runs locally at all.

The event loop *policy* API (set_event_loop_policy, WindowsSelectorEventLoopPolicy)
is the usual fix found online. It is deprecated in Python 3.14 and slated for
removal in 3.16, so this uses asyncio.run's loop_factory instead.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run(coro: Coroutine[Any, Any, T]) -> T:
    """asyncio.run on an event loop psycopg can actually use."""
    if sys.platform == "win32":
        # SelectorEventLoop has no subprocess support on Windows; this project
        # spawns none, so the trade is free.
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)
