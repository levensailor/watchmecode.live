"""Session PIN generation."""

import secrets

PIN_LENGTH = 6


def generate_pin() -> str:
    """Return a cryptographically random, zero-padded 6-digit PIN."""
    return f"{secrets.randbelow(10**PIN_LENGTH):0{PIN_LENGTH}d}"
