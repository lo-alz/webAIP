"""
ARINC 424 / NASR CIFP coordinate conversion — exact, no proximity tolerance.

Two input shapes occur in practice:

1. The *documented* shorthand from the spec (CLAUDE.md §4.1), hemisphere LAST,
   with a decimal point:  ``'340337.12N'`` / ``'1183159.78W'``.
2. The *native CIFP* packed form, hemisphere FIRST, no decimal point, seconds to
   hundredths:             ``'N34033712'`` / ``'W118315978'``.

Both reduce to the same decimal-degree value. We convert (2) -> (1) then run a
single exact conversion so there is exactly one source of truth for the maths.

All results are WGS-84 decimal degrees quantized to 8 dp (sub-centimetre,
lossless round-trip from the 0.01-arc-second CIFP source).
"""
from __future__ import annotations

import decimal

# 12 significant digits is ample headroom for DD + MM/60 + SS.ss/3600 at 8 dp.
decimal.getcontext().prec = 12

_Q8 = decimal.Decimal("0.00000001")


def arinc424_to_decimal(dms_str: str) -> decimal.Decimal:
    """
    Convert an ARINC 424 coordinate *shorthand* string to decimal degrees.

    Input formats (hemisphere suffix):
      Latitude:  'DDMMSS.SSN'  / 'DDMMSS.SSS'   (hemisphere N/S last)
      Longitude: 'DDDMMSS.SSE' / 'DDDMMSS.SSW'  (hemisphere E/W last)

    Returns Decimal quantized to 8 dp. South/West are negative.
    """
    dms_str = dms_str.strip()
    hem = dms_str[-1].upper()
    nums = dms_str[:-1]
    if hem in ("N", "S"):
        d = decimal.Decimal(nums[0:2])
        m = decimal.Decimal(nums[2:4])
        s = decimal.Decimal(nums[4:])
    elif hem in ("E", "W"):
        d = decimal.Decimal(nums[0:3])
        m = decimal.Decimal(nums[3:5])
        s = decimal.Decimal(nums[5:])
    else:
        raise ValueError(f"Unrecognised hemisphere in coordinate {dms_str!r}")

    result = d + m / 60 + s / 3600
    if hem in ("S", "W"):
        result = -result
    return result.quantize(_Q8)


def cifp_packed_to_shorthand(packed: str) -> str:
    """
    Convert native CIFP packed geo strings to the shorthand `arinc424_to_decimal`
    accepts.

      Latitude  'N34033712'  (1 + 8 digits: DD MM SS ss)  -> '340337.12N'
      Longitude 'W118315978' (1 + 9 digits: DDD MM SS ss) -> '1183159.78W'

    The trailing two digits are hundredths of an arc-second.
    """
    packed = packed.strip()
    hem = packed[0].upper()
    digits = packed[1:]
    if hem in ("N", "S"):
        if len(digits) != 8:
            raise ValueError(f"Latitude {packed!r} must be hemisphere + 8 digits")
        dd, mm, ss, frac = digits[0:2], digits[2:4], digits[4:6], digits[6:8]
    elif hem in ("E", "W"):
        if len(digits) != 9:
            raise ValueError(f"Longitude {packed!r} must be hemisphere + 9 digits")
        dd, mm, ss, frac = digits[0:3], digits[3:5], digits[5:7], digits[7:9]
    else:
        raise ValueError(f"Unrecognised hemisphere in packed coordinate {packed!r}")
    return f"{dd}{mm}{ss}.{frac}{hem}"


def cifp_packed_to_decimal(packed: str) -> decimal.Decimal:
    """Convenience: native CIFP packed geo string -> decimal degrees (8 dp)."""
    return arinc424_to_decimal(cifp_packed_to_shorthand(packed))


def canonical(value: decimal.Decimal) -> str:
    """Canonical 8-dp text form used as the lossless storage/equality key."""
    return str(decimal.Decimal(value).quantize(_Q8))
