#!/usr/bin/env python3
"""Compare records in a DNS zone file with records served by a name server."""

from __future__ import annotations

import argparse
import ipaddress
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import dns.exception
import dns.flags
import dns.message
import dns.query
import dns.rdatatype
import dns.zone


@dataclass(frozen=True)
class RecordSet:
    name: object
    rdtype: int
    expected: object


@dataclass(frozen=True)
class QueryResult:
    answer: object | None
    server: str


class QueryError(Exception):
    def __init__(self, attempts: list[tuple[str, str]]):
        self.attempts = attempts
        message = "; ".join(f"{server}: {error}" for server, error in attempts)
        super().__init__(message)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ゾーンファイルの値と指定ネームサーバーの応答を比較します。"
    )
    parser.add_argument("zone_file", type=Path, help="比較するゾーンファイル")
    parser.add_argument("nameserver", help="問い合わせ先ネームサーバー（IPアドレスまたはホスト名）")
    parser.add_argument(
        "--nameserver-family",
        choices=("auto", "ipv4", "ipv6"),
        default="auto",
        help="問い合わせ先ネームサーバーのアドレス種別（既定: auto）",
    )
    parser.add_argument(
        "--type",
        dest="types",
        action="append",
        metavar="TYPE",
        help="比較するレコードタイプ（複数回指定可。例: --type A --type MX）",
    )
    parser.add_argument(
        "--progress",
        nargs="?",
        const=20,
        type=int,
        metavar="N",
        help="進捗をN件ごと（および完了時）に表示。N省略時は20件",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="問い合わせのタイムアウト秒（既定: 5）")
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="問い合わせ間隔の秒数（既定: 0、小数指定可）",
    )
    parser.add_argument("--origin", help="$ORIGIN がないゾーンファイルのオリジン")
    return parser.parse_args(argv)


def socket_family(name: str) -> int:
    return {
        "auto": socket.AF_UNSPEC,
        "ipv4": socket.AF_INET,
        "ipv6": socket.AF_INET6,
    }[name]


def resolve_servers(server: str, family: str) -> list[str]:
    try:
        address = ipaddress.ip_address(server)
    except ValueError:
        try:
            infos = socket.getaddrinfo(
                server,
                53,
                family=socket_family(family),
                type=socket.SOCK_DGRAM,
            )
        except socket.gaierror as exc:
            raise ValueError(f"ネームサーバーを名前解決できません: {server}") from exc
        addresses: list[str] = []
        for info in infos:
            candidate = info[4][0]
            if candidate not in addresses:
                addresses.append(candidate)
        if not addresses:
            raise ValueError(f"ネームサーバーを名前解決できません: {server}")
        return addresses

    version = f"ipv{address.version}"
    if family != "auto" and family != version:
        raise ValueError(
            f"指定したネームサーバー {server} は {family} ではありません"
        )
    return [str(address)]


def load_recordsets(path: Path, origin: str | None, selected: list[str] | None) -> list[RecordSet]:
    wanted: set[int] | None = None
    if selected:
        wanted = set()
        for value in selected:
            for item in value.split(","):
                try:
                    wanted.add(dns.rdatatype.from_text(item.strip().upper()))
                except dns.exception.SyntaxError as exc:
                    raise ValueError(f"不正なレコードタイプです: {item}") from exc

    zone = dns.zone.from_file(
        str(path), origin=origin, relativize=False, check_origin=False
    )
    results: list[RecordSet] = []
    for name, node in zone.nodes.items():
        for rdataset in node.rdatasets:
            if wanted is None or rdataset.rdtype in wanted:
                results.append(RecordSet(name, rdataset.rdtype, rdataset))
    results.sort(key=lambda r: (r.name.canonicalize().to_wire(), r.rdtype))
    return results


def query(servers: list[str], record: RecordSet, timeout: float) -> QueryResult:
    request = dns.message.make_query(record.name, record.rdtype)
    attempts: list[tuple[str, str]] = []
    for server in servers:
        try:
            response = dns.query.udp(request, server, timeout=timeout)
            if response.flags & dns.flags.TC:
                response = dns.query.tcp(request, server, timeout=timeout)
        except (dns.exception.DNSException, OSError) as exc:
            attempts.append((server, describe_exception(exc)))
            continue

        for rrset in response.answer:
            if rrset.name == record.name and rrset.rdtype == record.rdtype:
                return QueryResult(rrset, server)
        return QueryResult(None, server)

    raise QueryError(attempts)


def key(rdata) -> bytes:
    return rdata.to_digestable()


def text(rdata, origin) -> str:
    return rdata.to_text(origin=origin, relativize=False)


def describe_exception(exc: Exception) -> str:
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc)


def run(args: argparse.Namespace) -> int:
    if args.timeout <= 0:
        raise ValueError("--timeout は 0 より大きい値を指定してください")
    if args.interval < 0:
        raise ValueError("--interval は 0 以上の値を指定してください")
    if args.progress is not None and args.progress <= 0:
        raise ValueError("--progress の件数は 1 以上を指定してください")
    records = load_recordsets(args.zone_file, args.origin, args.types)
    if not records:
        print("比較対象のレコードがありません。", file=sys.stderr)
        return 0
    servers = resolve_servers(args.nameserver, args.nameserver_family)
    differences = 0

    for index, record in enumerate(records, 1):
        if index > 1 and args.interval:
            time.sleep(args.interval)
        type_text = dns.rdatatype.to_text(record.rdtype)
        label = f"{record.name.to_text()} {type_text}"
        try:
            result = query(servers, record, args.timeout)
        except QueryError as exc:
            differences += 1
            print(f"ERROR {label}: {exc}")
            if args.progress and (index % args.progress == 0 or index == len(records)):
                print(
                    f"進捗: {index}/{len(records)} 件完了",
                    file=sys.stderr,
                    flush=True,
                )
            continue

        expected_by_key = {key(item): item for item in record.expected}
        actual_by_key = {key(item): item for item in result.answer} if result.answer else {}
        missing = expected_by_key.keys() - actual_by_key.keys()
        extra = actual_by_key.keys() - expected_by_key.keys()
        if missing or extra:
            differences += 1
            print(f"DIFF  {label} (server {result.server})")
            for item_key in sorted(missing):
                print(f"  - {text(expected_by_key[item_key], record.name)}")
            for item_key in sorted(extra):
                print(f"  + {text(actual_by_key[item_key], record.name)}")
        if args.progress and (index % args.progress == 0 or index == len(records)):
            print(
                f"進捗: {index}/{len(records)} 件完了",
                file=sys.stderr,
                flush=True,
            )

    if differences:
        print(f"\n{differences} 件のレコードセットに差分またはエラーがありました。")
        return 1
    print(f"OK: {len(records)} 件のレコードセットが一致しました。")
    return 0


def main() -> int:
    try:
        return run(parse_args())
    except (OSError, ValueError, dns.exception.DNSException) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
