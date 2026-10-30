import unittest

from turn_context import should_apply_advertised_context


class AdvertisedContextTests(unittest.TestCase):
    def test_metadata_supplies_default_only_when_no_session_or_user_choice_exists(self):
        self.assertTrue(should_apply_advertised_context(False, False))
        self.assertFalse(should_apply_advertised_context(True, False))
        self.assertFalse(should_apply_advertised_context(False, True))


if __name__ == "__main__":
    unittest.main()
