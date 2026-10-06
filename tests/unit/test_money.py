import unittest

from ledgerlite.domain.errors import ValidationError
from ledgerlite.domain.money import MAX_AMOUNT, format_amount, parse_amount


class ParseAmountTest(unittest.TestCase):
    def test_valid_amounts(self):
        cases = {
            "0": 0,
            "0.00": 0,
            "1": 100,
            "12.5": 1250,
            "12.50": 1250,
            "0.05": 5,
            "100.99": 10099,
            "-3.20": -320,
            "99999999999.99": MAX_AMOUNT,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_amount(text), expected)

    def test_invalid_amounts(self):
        for text in ["", "abc", "1.234", "1,50", " 1.00", "1.00 ", "1e3", ".5", "5.", "+5", "١٢٣",
                     "123456789012", "--1"]:
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_amount(text)

    def test_non_string_inputs_are_rejected(self):
        for value in [12.5, 12, None, True, ["1.00"]]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_amount(value)


class FormatAmountTest(unittest.TestCase):
    def test_format(self):
        cases = {0: "0.00", 5: "0.05", 100: "1.00", 1250: "12.50", -5: "-0.05", -123456: "-1234.56"}
        for minor, expected in cases.items():
            with self.subTest(minor=minor):
                self.assertEqual(format_amount(minor), expected)

    def test_roundtrip_examples(self):
        for minor in [0, 1, 99, 100, 101, -1, -250, MAX_AMOUNT, -MAX_AMOUNT]:
            with self.subTest(minor=minor):
                self.assertEqual(parse_amount(format_amount(minor)), minor)
