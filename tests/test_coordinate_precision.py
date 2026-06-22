"""ARINC 424 DMS -> decimal degrees: exact, lossless conversion."""
import decimal

import pytest

from miner.coordinates import (
    arinc424_to_decimal,
    canonical,
    cifp_packed_to_decimal,
    cifp_packed_to_shorthand,
)


def test_shorthand_latitude_exact():
    # 34°03'37.12"N = 34 + 3/60 + 37.12/3600
    assert arinc424_to_decimal("340337.12N") == decimal.Decimal("34.06031111")


def test_shorthand_longitude_west_is_negative():
    assert arinc424_to_decimal("1183159.78W") == decimal.Decimal("-118.53327222")


def test_southern_hemisphere_negative():
    assert arinc424_to_decimal("340337.12S") == decimal.Decimal("-34.06031111")


def test_eight_decimal_places_quantization():
    v = arinc424_to_decimal("000000.00N")
    assert str(v) == "0E-8" or v == decimal.Decimal("0.00000000")


def test_packed_to_shorthand_lat():
    assert cifp_packed_to_shorthand("N34033712") == "340337.12N"


def test_packed_to_shorthand_lon():
    # W DDD MM SS ss = 118°24'21.58"W
    assert cifp_packed_to_shorthand("W118242158") == "1182421.58W"


def test_packed_round_trip_matches_shorthand():
    packed = cifp_packed_to_decimal("N34033712")
    short = arinc424_to_decimal("340337.12N")
    assert packed == short


def test_canonical_is_eight_dp_text():
    assert canonical(decimal.Decimal("34.0603111111")) == "34.06031111"


def test_bad_hemisphere_raises():
    with pytest.raises(ValueError):
        arinc424_to_decimal("340337.12X")
