import unittest

from turn_context import parse_run_settings


class RunSettingsTests(unittest.TestCase):
    def test_accepts_high_context_output_and_temperature_values(self):
        self.assertEqual(parse_run_settings("262144", "8192", "0.7"), (262144, 8192, 0.7))

    def test_rejects_temperature_outside_supported_range(self):
        with self.assertRaisesRegex(ValueError, "temperature"):
            parse_run_settings("262144", "8192", "2.1")
        with self.assertRaisesRegex(ValueError, "temperature"):
            parse_run_settings("262144", "8192", "nan")

    def test_rejects_invalid_context_and_output_limits(self):
        with self.assertRaisesRegex(ValueError, "Context"):
            parse_run_settings("0", "8192", "0.7")
        with self.assertRaisesRegex(ValueError, "output"):
            parse_run_settings("262144", "0", "0.7")

    def test_accepts_positive_context_below_the_old_artificial_minimum(self):
        self.assertEqual(parse_run_settings("512", "8192", "0.7"), (512, 8192, 0.7))


if __name__ == "__main__":
    unittest.main()
