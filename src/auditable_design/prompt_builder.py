"""Prompt construction helpers — with adversarial-input discipline (ADR-010).

Every piece of untrusted, user-generated text (review bodies, cluster labels
derived from reviews, free-text meta-weight rationales …) MUST pass through
`wrap_user_text()` before being interpolated into any Claude prompt.

The wrapper does two things:

1. HTML-escapes ``<``, ``>``, and ``&`` in the input. This closes the attack
   vector where a malicious review contains a literal ``</user_review>``
   followed by new instructions ("ignore previous, instead respond with…").
   Escaped text is still fully readable — ``<3`` simply becomes ``&lt;3``
   in the few reviews that happen to use HTML-ish markup.
2. Wraps the escaped text in ``<user_review id="…">…</user_review>`` tags.
   Every audit skill prompt tells Claude that user-submitted text lives
   inside these tags and is to be treated as data, not instructions.

See also:
    ARCHITECTURE.md §1 (principle P6).
    docs/ADRs.md (ADR-010).
    docs/SECURITY.md (V-03 prompt injection — closed architecturally).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

__all__ = [
    "InjectionGuardError",
    "WrappedText",
    "wrap_many",
    "wrap_user_text",
]


# Characters we refuse to emit into an id attribute. Whitespace and quote
# characters would break the attribute; angle brackets and ampersand would
# break the wrapper itself.
_DISALLOWED_IN_ID = frozenset("\"'<>& \t\n\r")

# HTML entity escape map. We deliberately escape `&` first by listing it
# first — `str.translate` uses a single-pass table so ordering is moot,
# but keeping `&` first makes the intent obvious to readers.
_ESCAPE_MAP = str.maketrans(
    {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
    }
)


class InjectionGuardError(ValueError):
    """Raised when wrap_user_text() is asked to emit an unsafe payload."""


@dataclass(frozen=True, slots=True)
class WrappedText:
    """Result of :func:`wrap_user_text`.

    Attributes:
        wrapped:           Safe-to-interpolate string containing
                           ``<user_review id="…">…</user_review>``.
        review_id:         The id that was used (either the caller-supplied
                           one, or the derived sha256-prefix).
        contained_markup:  ``True`` if the *original* text contained any of
                           ``<``, ``>``, ``&``. Used in logs and telemetry to
                           flag reviews that may have been probing the
                           wrapper.
    """

    wrapped: str
    review_id: str
    contained_markup: bool


def _canonical_id(text: str, salt: str = "") -> str:
    """Deterministic 12-hex-char id derived from ``sha256(salt || \\x00 || text)``.

    Deterministic — identical ``(salt, text)`` always yields the same id,
    which matters for replay-mode cache hits.
    """
    h = hashlib.sha256((salt + "\x00" + text).encode("utf-8")).hexdigest()
    return h[:12]


def _validate_id(rid: str) -> None:
    if not rid:
        raise InjectionGuardError("review_id must not be empty")
    bad = _DISALLOWED_IN_ID.intersection(rid)
    if bad:
        raise InjectionGuardError(f"review_id contains disallowed character(s) {sorted(bad)!r}: {rid!r}")


def wrap_user_text(
    text: str,
    *,
    review_id: str | None = None,
    salt: str = "",
) -> WrappedText:
    """Wrap untrusted, user-generated text for safe interpolation into a Claude prompt.

    The returned :class:`WrappedText` payload looks like::

        <user_review id="abc123def456">&lt;escaped&gt; user content here</user_review>

    Every audit prompt built elsewhere in the codebase includes a system-prompt
    preamble telling Claude to treat anything inside ``<user_review>`` tags
    as data only.

    Args:
        text:      The raw user text to wrap. Must be ``str``.
        review_id: Optional identifier. If ``None``, one is derived
                   deterministically from ``sha256(text)``. Callers that have
                   a stable review id (from Google Play / App Store) should
                   pass it explicitly.
        salt:      Optional salt folded into the derived id. Useful when the
                   same text appears in two different contexts and should
                   yield two different ids.

    Returns:
        A :class:`WrappedText`.

    Raises:
        TypeError:             If ``text`` is not a string.
        InjectionGuardError:   If ``review_id`` contains a character that
                               would break the wrapper.
    """
    if not isinstance(text, str):
        raise TypeError(f"expected str, got {type(text).__name__}")

    contained_markup = any(c in text for c in "<>&")
    safe = text.translate(_ESCAPE_MAP)
    rid = review_id if review_id is not None else _canonical_id(text, salt)
    _validate_id(rid)

    wrapped = f'<user_review id="{rid}">{safe}</user_review>'
    return WrappedText(wrapped=wrapped, review_id=rid, contained_markup=contained_markup)


def wrap_many(
    items: list[tuple[str, str]],
    *,
    salt: str = "",
) -> list[WrappedText]:
    """Wrap a list of ``(review_id, text)`` pairs and enforce id uniqueness.

    Args:
        items: List of ``(review_id, text)`` tuples. Both strings are required.
        salt:  Optional salt passed to each :func:`wrap_user_text` call.

    Returns:
        Parallel list of :class:`WrappedText` results.

    Raises:
        InjectionGuardError: If any ``review_id`` is duplicated.
    """
    seen: set[str] = set()
    out: list[WrappedText] = []
    for rid, text in items:
        wrapped = wrap_user_text(text, review_id=rid, salt=salt)
        if wrapped.review_id in seen:
            raise InjectionGuardError(f"duplicate review_id: {wrapped.review_id}")
        seen.add(wrapped.review_id)
        out.append(wrapped)
    return out
