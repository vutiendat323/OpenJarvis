"""Builds the repl namespace's evidence reader from plain data only.

``repl`` must be able to read prior tool output but never gain a path back to
the evidence store ``display_payment_qr`` trusts. Binding a function that
closes over ``evidence.record`` (or that even imports the ``evidence``
module) is unsafe no matter how the function body reads: every Python
function exposes its defining module's globals through ``__globals__``, so
interpreted code can always do ``last_result.__globals__['record']`` and
forge provenance for a fake payment QR.

The fix is structural: this module imports nothing but ``typing``, and the
factory below closes over plain ``str`` / ``dict[str, str]`` values handed to
it by the caller. After that, ``last_result.__globals__`` leads only to this
harmless module and ``last_result.__closure__`` holds strings -- there is
nothing sensitive left to reach.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional


def make_last_result(
    snapshot: Dict[str, str], most_recent: str
) -> Callable[[Optional[str]], str]:
    """Build a ``last_result(tool_name=None)`` reader over a plain-data snapshot.

    ``snapshot`` maps tool name to that tool's most recent content;
    ``most_recent`` is the most recent content from any tool. Both are taken
    once, at call time -- the returned function is a read-only view of that
    moment, not a live lookup.
    """

    def last_result(tool_name: Optional[str] = None) -> str:
        if tool_name is None:
            return most_recent
        return snapshot.get(tool_name, "")

    return last_result


__all__ = ["make_last_result"]
