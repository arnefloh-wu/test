"""Command-line interface: ``wos search 'TS=("psychic distance")' -o out.csv``."""

from __future__ import annotations

import argparse
import csv
import json
import sys

from .client import WosClient, WosError
from .export import write_wos_plaintext
from .parse import COLUMNS, flatten

FORMATS = ("csv", "jsonl", "wos")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="wos", description="Web of Science Expanded API client")
    p.add_argument("--api-key", help="defaults to $WOS_API_KEY")
    p.add_argument("--database", default="WOS", help="databaseId (default: WOS core collection)")
    p.add_argument("--base-url", help="API base URL; defaults to $WOS_API_URL or "
                                      "https://api.clarivate.com/api/wos")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="run an advanced-search query and export records")
    s.add_argument("query", help='WoS advanced search, e.g. TS=("export performance") AND PY=2015-2024')
    _output_args(s)
    s.add_argument("-n", "--max-records", type=int, help="stop after N records")
    s.add_argument("--edition", help='e.g. "WOS+SSCI" to limit to the Social Sciences Citation Index')
    s.add_argument("--sort", help='sortField, e.g. "TC+D" (most cited first) or "PY+D"')
    s.add_argument("--fetch-references", action="store_true",
                   help="for the wos format, fetch the full cited-reference list from the API "
                        "when a record's embedded list is incomplete (one request per record)")

    v = sub.add_parser("convert", help="convert raw records saved with --raw to another format")
    v.add_argument("input", help="a .jsonl file written by 'wos search ... --raw'")
    _output_args(v)

    c = sub.add_parser("count", help="print the number of records matching a query")
    c.add_argument("query")
    c.add_argument("--edition")

    args = p.parse_args(argv)
    try:
        if args.cmd == "convert":
            with open(args.input, encoding="utf-8") as f:
                records = (json.loads(line) for line in f if line.strip())
                n = _write(records, args.output, _format(args), raw=args.raw)
            print(f"wrote {n} records", file=sys.stderr)
            return 0

        client = WosClient(args.api_key, database=args.database, base_url=args.base_url)
        if args.cmd == "count":
            print(client.count(args.query, edition=args.edition))
            return 0
        records = client.search(args.query, max_records=args.max_records,
                                edition=args.edition, sort_field=args.sort)
        fetch = client.references if args.fetch_references else None
        n = _write(records, args.output, _format(args), raw=args.raw, fetch_references=fetch)
    except (WosError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {n} records"
          + (f"; {client.records_remaining} records left in yearly quota"
             if client.records_remaining is not None else ""),
          file=sys.stderr)
    return 0


def _output_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("-o", "--output", help="output file; default stdout. The format follows the "
                                          "extension: .csv, .jsonl, or .txt (WoS plain text)")
    p.add_argument("-f", "--format", choices=FORMATS,
                   help="override the format: csv, jsonl, or wos (WoS plain-text export "
                        "for VOSviewer / Bibliometrix)")
    p.add_argument("--raw", action="store_true", help="with jsonl, write raw records instead of flat rows")


def _format(args) -> str:
    if args.format:
        return args.format
    path = (args.output or "").lower()
    if path.endswith(".jsonl"):
        return "jsonl"
    if path.endswith(".txt"):
        return "wos"
    return "csv"


def _write(records, path: str | None, fmt: str, *, raw: bool, fetch_references=None) -> int:
    out = open(path, "w", newline="" if fmt == "csv" else None, encoding="utf-8") if path else sys.stdout
    n = 0
    try:
        if fmt == "wos":
            n = write_wos_plaintext(records, out, fetch_references=fetch_references)
        elif fmt == "jsonl":
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
