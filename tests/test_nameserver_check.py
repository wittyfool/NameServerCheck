import contextlib
import io
import socket
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
            nameserver_family="auto",
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

    @patch("nameserver_check.socket.getaddrinfo")
    def test_resolve_servers_uses_requested_address_family(self, mock_getaddrinfo):
        # Duplicate results simulate resolver output and verify order-preserving deduplication.
        mock_getaddrinfo.return_value = [
            (socket.AF_INET6, socket.SOCK_DGRAM, 17, "", ("2001:db8::53", 53, 0, 0)),
            (socket.AF_INET6, socket.SOCK_DGRAM, 17, "", ("2001:db8::54", 53, 0, 0)),
            (socket.AF_INET6, socket.SOCK_DGRAM, 17, "", ("2001:db8::53", 53, 0, 0)),
            (socket.AF_INET6, socket.SOCK_DGRAM, 17, "", ("2001:db8::54", 53, 0, 0)),
        ]

        result = nameserver_check.resolve_servers("ns1.example.test", "ipv6")

        self.assertEqual(["2001:db8::53", "2001:db8::54"], result)
        mock_getaddrinfo.assert_called_once_with(
            "ns1.example.test",
            53,
            family=socket.AF_INET6,
            type=socket.SOCK_DGRAM,
        )

    def test_resolve_servers_rejects_mismatched_literal_family(self):
        with self.assertRaisesRegex(ValueError, "ipv6"):
            nameserver_check.resolve_servers("192.0.2.53", "ipv6")

    @patch("nameserver_check.dns.query.udp")
    def test_query_falls_back_to_next_resolved_server(self, mock_udp):
        record = nameserver_check.load_recordsets(self.zone_path, None, ["A"])[0]

        def side_effect(request, server, timeout):
            if server == "2001:db8::53":
                raise OSError("Network is unreachable")
            response = nameserver_check.dns.message.make_response(request)
            response.answer.append(
                dns.rrset.from_text(record.name, 300, "IN", "A", "192.0.2.10")
            )
            return response

        mock_udp.side_effect = side_effect

        result = nameserver_check.query(
            ["2001:db8::53", "192.0.2.53"], record, 1.0
        )

        self.assertEqual("192.0.2.53", result.server)
        self.assertEqual("192.0.2.10", result.answer[0].address)

    @patch("nameserver_check.query")
    def test_matching_records_return_zero(self, mock_query):
        mock_query.side_effect = lambda servers, record, timeout: nameserver_check.QueryResult(record.expected, servers[0])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = nameserver_check.run(self.args(types=["A"]))
        self.assertEqual(0, result)
        self.assertIn("2 件", output.getvalue())

    @patch("nameserver_check.query")
    def test_difference_returns_one_and_shows_both_sides(self, mock_query):
        def answer(servers, record, timeout):
            if record.name.to_text() == "www.example.test.":
                return nameserver_check.QueryResult(
                    dns.rrset.from_text(
                        record.name, 300, "IN", "A", "192.0.2.99"
                    ),
                    servers[0],
                )
            return nameserver_check.QueryResult(record.expected, servers[0])

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
        mock_query.side_effect = lambda servers, record, timeout: nameserver_check.QueryResult(record.expected, servers[0])
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
        mock_query.side_effect = lambda servers, record, timeout: nameserver_check.QueryResult(record.expected, servers[0])
        error = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(error):
            result = nameserver_check.run(self.args(types=["A"], progress=10))
        self.assertEqual(0, result)
        self.assertEqual("進捗: 2/2 件完了", error.getvalue().strip())

    @patch("nameserver_check.resolve_servers")
    @patch("nameserver_check.query")
    def test_query_failure_shows_attempted_servers(self, mock_query, mock_resolve_servers):
        mock_resolve_servers.return_value = ["2001:db8::53", "192.0.2.53"]
        mock_query.side_effect = nameserver_check.QueryError([
            ("2001:db8::53", "Network is unreachable"),
            ("192.0.2.53", "timed out"),
        ])
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            result = nameserver_check.run(self.args(types=["A"]))

        self.assertEqual(1, result)
        self.assertIn("2001:db8::53", output.getvalue())
        self.assertIn("192.0.2.53", output.getvalue())

    def test_zero_progress_interval_is_rejected(self):
        args = self.args(types=["A"], progress=0)
        with self.assertRaisesRegex(ValueError, "--progress"):
            nameserver_check.run(args)


if __name__ == "__main__":
    unittest.main()
