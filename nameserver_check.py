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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ゾーンファイルの値と指定ネームサーバーの応答を比較します。"
    )
    parser.add_argument("zone_file", type=Path, help="比較するゾーンファイル")
    parser.add_argument("nameserver", help="問い合わせ先ネームサーバー（IPアドレスまたはホスト名）")
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


def resolve_server(server: str) -> str:
    try:
        return str(ipaddress.ip_address(server))
    except ValueError:
        infos = socket.getaddrinfo(server, 53, type=socket.SOCK_DGRAM)
        if not infos:
            raise RuntimeError(f"ネームサーバーを名前解決できません: {server}")
        return infos[0][4][0]


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


def query(server: str, record: RecordSet, timeout: float):
    request = dns.message.make_query(record.name, record.rdtype)
    response = dns.query.udp(request, server, timeout=timeout)
    if response.flags & dns.flags.TC:
        response = dns.query.tcp(request, server, timeout=timeout)
    for rrset in response.answer:
        if rrset.name == record.name and rrset.rdtype == record.rdtype:
            return rrset
    return None


def key(rdata) -> bytes:
    return rdata.to_digestable()


def text(rdata, origin) -> str:
    return rdata.to_text(origin=origin, relativize=False)


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
    server = resolve_server(args.nameserver)
    differences = 0

    for index, record in enumerate(records, 1):
        if index > 1 and args.interval:
            time.sleep(args.interval)
        type_text = dns.rdatatype.to_text(record.rdtype)
        label = f"{record.name.to_text()} {type_text}"
        try:
            actual = query(server, record, args.timeout)
        except (dns.exception.DNSException, OSError) as exc:
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
        actual_by_key = {key(item): item for item in actual} if actual else {}
        missing = expected_by_key.keys() - actual_by_key.keys()
        extra = actual_by_key.keys() - expected_by_key.keys()
        if missing or extra:
            differences += 1
            print(f"DIFF  {label}")
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
