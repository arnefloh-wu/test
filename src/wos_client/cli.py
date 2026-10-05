"""Command-line interface: ``wos search 'TS=("psychic distance")' -o out.csv``."""

from __future__ import annotations

import argparse
import csv
import json
import sys

from .client import WosClient, WosError
from .parse import COLUMNS, flatten


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="wos", description="Web of Science Expanded API client")
    p.add_argument("--api-key", help="defaults to $WOS_API_KEY")
    p.add_argument("--database", default="WOS", help="databaseId (default: WOS core collection)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="run an advanced-search query and export records")
    s.add_argument("query", help='WoS advanced search, e.g. TS=("export performance") AND PY=2015-2024')
    s.add_argument("-o", "--output", help="output file (.csv or .jsonl); default stdout CSV")
    s.add_argument("-n", "--max-records", type=int, help="stop after N records")
    s.add_argument("--edition", help='e.g. "WOS+SSCI" to limit to the Social Sciences Citation Index')
    s.add_argument("--sort", help='sortField, e.g. "TC+D" (most cited first) or "PY+D"')
    s.add_argument("--raw", action="store_true", help="with .jsonl, write raw records instead of flat rows")

    c = sub.add_parser("count", help="print the number of records matching a query")
    c.add_argument("query")
    c.add_argument("--edition")

    args = p.parse_args(argv)
    try:
        client = WosClient(args.api_key, database=args.database)
        if args.cmd == "count":
            print(client.count(args.query, edition=args.edition))
            return 0
        records = client.search(args.query, max_records=args.max_records,
                                edition=args.edition, sort_field=args.sort)
        n = _write(records, args.output, raw=args.raw)
    except (WosError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {n} records"
          + (f"; {client.records_remaining} records left in yearly quota"
             if client.records_remaining is not None else ""),
          file=sys.stderr)
    return 0


def _write(records, path: str | None, *, raw: bool) -> int:
    jsonl = bool(path) and path.endswith(".jsonl")
    out = open(path, "w", newline="", encoding="utf-8") if path else sys.stdout
    n = 0
    try:
        if jsonl:
            for rec in records:
                out.write(json.dumps(rec if raw else flatten(rec), ensure_ascii=False) + "\n")
                n += 1
        else:
            w = csv.DictWriter(out, fieldnames=COLUMNS)
            w.writeheader()
            for rec in records:
                w.writerow(flatten(rec))
                n += 1
    finally:
        if path:
            out.close()
    return n


if __name__ == "__main__":
    sys.exit(main())
