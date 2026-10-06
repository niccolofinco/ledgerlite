import unittest

from hypothesis import given
from hypothesis import strategies as st
from ledgerlite.domain.errors import ValidationError
from ledgerlite.domain.models import Posting, Transaction
from ledgerlite.domain.money import MAX_AMOUNT, format_amount, parse_amount

from tests.helpers import NOW, TODAY
from tests.property.common import PROPERTY_SETTINGS


class MoneyProperties(unittest.TestCase):
    @PROPERTY_SETTINGS
    @given(st.integers(-MAX_AMOUNT, MAX_AMOUNT))
    def test_format_then_parse_is_identity(self, minor_units):
        self.assertEqual(parse_amount(format_amount(minor_units)), minor_units)

    @PROPERTY_SETTINGS
    @given(st.integers(0, 10**11 - 1), st.integers(0, 99))
    def test_parse_then_format_is_identity_on_canonical_strings(self, whole, cents):
        text = f"{whole}.{cents:02d}"
        self.assertEqual(format_amount(parse_amount(text)), text)

    @PROPERTY_SETTINGS
    @given(st.integers(0, 10**11 - 1), st.integers(0, 99))
    def test_parsing_is_sign_symmetric(self, whole, cents):
        text = f"{whole}.{cents:02d}"
        self.assertEqual(parse_amount("-" + text), -parse_amount(text))

    @PROPERTY_SETTINGS
    @given(st.integers(0, 10**11 - 1), st.integers(0, 99))
    def test_a_single_decimal_digit_means_tens_of_cents(self, whole, tenths):
        self.assertEqual(parse_amount(f"{whole}.{tenths % 10}"), whole * 100 + (tenths % 10) * 10)


class TransactionProperties(unittest.TestCase):
    @PROPERTY_SETTINGS
    @given(st.lists(st.integers(-1000, 1000).filter(lambda n: n != 0), min_size=2, max_size=8))
    def test_a_transaction_is_valid_if_and_only_if_it_is_balanced(self, amounts):
        postings = tuple(Posting(f"acc-{i % 3}", amount) for i, amount in enumerate(amounts))
        try:
            Transaction("t", TODAY, "generated", postings, NOW)
            accepted = True
        except ValidationError:
            accepted = False
        self.assertEqual(accepted, sum(amounts) == 0)
