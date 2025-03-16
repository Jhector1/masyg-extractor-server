import re
import unittest

from masyg_extractor.utils.tool import remove_sensitive_data


# --- Code to be tested ---


# --- Test Cases ---
class TestRemoveSensitiveData(unittest.TestCase):

    def test_email_removal(self):
        input_text = "Contact me at alice@example.com."
        expected_text = "Contact me at [EMAIL]."
        expected_sensitive = {"EMAIL": ["alice@example.com"]}

        cleaned_text, sensitive = remove_sensitive_data(input_text)
        self.assertEqual(cleaned_text, expected_text)
        self.assertEqual(sensitive, expected_sensitive)

    def test_phone_removal(self):
        input_text = "Call 1234567890 for info."
        expected_text = "Call [PHONE] for info."
        expected_sensitive = {"PHONE": ["1234567890"]}

        cleaned_text, sensitive = remove_sensitive_data(input_text)
        self.assertEqual(cleaned_text, expected_text)
        self.assertEqual(sensitive, expected_sensitive)

    def test_credit_card_removal(self):
        input_text = "My card is 1234-5678-9012-3456."
        expected_text = "My card is [CREDIT_CARD]."
        expected_sensitive = {"CREDIT_CARD": ["1234-5678-9012-3456"]}

        cleaned_text, sensitive = remove_sensitive_data(input_text)
        self.assertEqual(cleaned_text, expected_text)
        self.assertEqual(sensitive, expected_sensitive)

    def test_address_removal(self):
        input_text = "I live at 123 Main Street."
        expected_text = "I live at [ADDRESS]."
        expected_sensitive = {"ADDRESS": ["123 Main Street"]}

        cleaned_text, sensitive = remove_sensitive_data(input_text)
        self.assertEqual(cleaned_text, expected_text)
        self.assertEqual(sensitive, expected_sensitive)

    def test_invoice_removal(self):
        input_text = "See Invoice #7890 for details."
        expected_text = "See [INVOICE] for details."
        expected_sensitive = {"INVOICE": ["Invoice #7890"]}

        cleaned_text, sensitive = remove_sensitive_data(input_text)
        self.assertEqual(cleaned_text, expected_text)
        self.assertEqual(sensitive, expected_sensitive)

    def test_multiple_removals(self):
        input_text = (
            "Please email john.doe@example.com and jane.doe@example.com. "
            "Call 1234567890 or 0987654321. "
            "Cards: 1234-5678-9012-3456 and 9876 5432 1098 7654. "
            "Addresses: 123 Main Street and 456 Elm Road. "
            "Invoice #1234 was paid."
        )
        expected_text = (
            "Please email [EMAIL] and [EMAIL]. "
            "Call [PHONE] or [PHONE]. "
            "Cards: [CREDIT_CARD] and [CREDIT_CARD]. "
            "Addresses: [ADDRESS] and [ADDRESS]. "
            "[INVOICE] was paid."
        )
        expected_sensitive = {
            "EMAIL": ["john.doe@example.com", "jane.doe@example.com"],
            "PHONE": ["1234567890", "0987654321"],
            "CREDIT_CARD": ["1234-5678-9012-3456", "9876 5432 1098 7654"],
            "ADDRESS": ["123 Main Street", "456 Elm Road"],
            "INVOICE": ["Invoice #1234"]
        }

        cleaned_text, sensitive = remove_sensitive_data(input_text)
        self.assertEqual(cleaned_text, expected_text)
        self.assertEqual(sensitive, expected_sensitive)

    def test_no_sensitive_data(self):
        input_text = "There is no sensitive info here."
        expected_text = input_text
        expected_sensitive = {}

        cleaned_text, sensitive = remove_sensitive_data(input_text)
        self.assertEqual(cleaned_text, expected_text)
        self.assertEqual(sensitive, expected_sensitive)


if __name__ == '__main__':
    unittest.main()
