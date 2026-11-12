"""Identifier helpers."""

import itertools
import re
import secrets
import time

# Crockford base32: no I, L, O or U, so IDs survive being read out or retyped.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LENGTH = 12


def generate_interview_id() -> str:
    """Generates an ID such as ``7K2M-9QXT-4HVB``, carrying 60 random bits."""
    raw = secrets.token_bytes(_LENGTH)
    out = ""
    for i, byte in enumerate(raw):
        out += _ALPHABET[byte & 31]
        if i in (3, 7):
            out += "-"
    return out


def normalize_interview_id(value: str) -> str:
    """Tolerates what people do when typing an ID: lower case, spaces and look-alike letters."""
    compact = re.sub(r"[\s-]", "", value.upper()).replace("O", "0")
    compact = re.sub(r"[IL]", "1", compact)
    if len(compact) == _LENGTH:
        return f"{compact[:4]}-{compact[4:8]}-{compact[8:]}"
    return compact


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).lower()


_counter = itertools.count()


def new_id() -> str:
    """Row id: time-ordered (millisecond + counter) with random tail, so rows created in the
    same millisecond still sort in creation order."""
    millis = int(time.time() * 1000)
    seq = next(_counter) % (36**3)

    def b36(number: int, width: int) -> str:
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        out = ""
        while number:
            number, rem = divmod(number, 36)
            out = digits[rem] + out
        return out.rjust(width, "0")

    return f"c{b36(millis, 9)}{b36(seq, 3)}{secrets.token_hex(5)}"
