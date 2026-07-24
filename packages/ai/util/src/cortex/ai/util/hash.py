"""Fast deterministic hash to shorten long strings.

Port of hoocode's `packages/ai/src/utils/hash.ts`.
"""


def _to_base36(n: int) -> str:
    """Convert an integer to base-36 string."""
    if n == 0:
        return "0"
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = []
    while n:
        result.append(chars[n % 36])
        n //= 36
    return "".join(reversed(result))


def short_hash(s: str) -> str:
    """Fast deterministic hash to shorten long strings.

    Args:
        s: The string to hash.

    Returns:
        A short base-36 encoded hash.
    """
    h1 = 0xDEADBEEF
    h2 = 0x41C6CE57

    for ch in s:
        c = ord(ch)
        h1 = (h1 ^ c) * 2654435761 & 0xFFFFFFFF
        h2 = (h2 ^ c) * 1597334677 & 0xFFFFFFFF

    h1 = ((h1 ^ (h1 >> 16)) * 2246822507 & 0xFFFFFFFF) ^ (
        (h2 ^ (h2 >> 13)) * 3266489909 & 0xFFFFFFFF
    )
    h2 = ((h2 ^ (h2 >> 16)) * 2246822507 & 0xFFFFFFFF) ^ (
        (h1 ^ (h1 >> 13)) * 3266489909 & 0xFFFFFFFF
    )

    return _to_base36(h2 & 0xFFFFFFFF) + _to_base36(h1 & 0xFFFFFFFF)
