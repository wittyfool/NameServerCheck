import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dns.rrset

import nameserver_check


ZONE = """$ORIGIN example.test.
$TTL 300
@ IN SOA ns1.example.test. hostmaster.example.test. 1 3600 600 86400 300
@ IN NS ns1.example.test.
@ IN A 192.0.2.10
www IN A 192.0.2.20
@ IN MX 10 mail.example.test.
"""


class NameServerCheckTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.zone_path = Path(self.tempdir.name) / "example.zone"
        self.zone_path.write_text(ZONE, encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    def args(self, types=None, progress=None):
        return SimpleNamespace(
            zone_file=self.zone_path,
            nameserver="192.0.2.53",
            types=types,
            progress=progress,
            timeout=1.0,
            interval=0.0,
            origin=None,
        )

    def test_type_filter_accepts_repeated_and_comma_separated_values(self):
        records = nameserver_check.load_recordsets(
            self.zone_path, None, ["A,MX"]
        )
        self.assertEqual(["A", "A", "MX"], [
            nameserver_check.dns.rdatatype.to_text(r.rdtype) for r in records
        ])

    @patch("nameserver_check.query")
    def test_matching_records_return_zero(self, mock_query):
        mock_query.side_effect = lambda server, record, timeout: record.expected
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = nameserver_check.run(self.args(types=["A"]))
        self.assertEqual(0, result)
        self.assertIn("2 件", output.getvalue())

    @patch("nameserver_check.query")
    def test_difference_returns_one_and_shows_both_sides(self, mock_query):
        def answer(server, record, timeout):
            if record.name.to_text() == "www.example.test.":
                return dns.rrset.from_text(
                    record.name, 300, "IN", "A", "192.0.2.99"
                )
            return record.expected

        mock_query.side_effect = answer
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = nameserver_check.run(self.args(types=["A"]))
        self.assertEqual(1, result)
        self.assertIn("- 192.0.2.20", output.getvalue())
        self.assertIn("+ 192.0.2.99", output.getvalue())

    @patch("nameserver_check.time.sleep")
    @patch("nameserver_check.query")
    def test_interval_waits_between_queries(self, mock_query, mock_sleep):
        mock_query.side_effect = lambda server, record, timeout: record.expected
        args = self.args(types=["A"])
        args.interval = 1.0
        with contextlib.redirect_stdout(io.StringIO()):
            result = nameserver_check.run(args)
        self.assertEqual(0, result)
        self.assertEqual([unittest.mock.call(1.0)], mock_sleep.call_args_list)

    def test_negative_interval_is_rejected(self):
        args = self.args(types=["A"])
        args.interval = -1
        with self.assertRaisesRegex(ValueError, "--interval"):
            nameserver_check.run(args)

    @patch("nameserver_check.query")
    def test_progress_is_shown_at_completion(self, mock_query):
        mock_query.side_effect = lambda server, record, timeout: record.expected
        error = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(error):
            result = nameserver_check.run(self.args(types=["A"], progress=10))
        self.assertEqual(0, result)
        self.assertEqual("進捗: 2/2 件完了", error.getvalue().strip())

    def test_zero_progress_interval_is_rejected(self):
        args = self.args(types=["A"], progress=0)
        with self.assertRaisesRegex(ValueError, "--progress"):
            nameserver_check.run(args)


if __name__ == "__main__":
    unittest.main()
