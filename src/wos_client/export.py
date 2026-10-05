"""Write records in the Web of Science plain-text export format ("savedrecs.txt").

This is the "Plain text file / Full record and cited references" format from
the WoS web interface, which both bibliometric tools read directly:

* VOSviewer: Create map > Read data from bibliographic database files > Web of Science
* Bibliometrix (R): ``convert2df("savedrecs.txt", dbsource = "wos", format = "plaintext")``

Each field is a two-letter tag followed by its value. Multi-valued fields (AU,
AF, C1, CR) continue on lines indented by three spaces, and every record ends
with ``ER``.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, TextIO

from .parse import _get, _int, _list, _text, flatten

HEADER = "﻿FN Clarivate Analytics Web of Science\nVR 1.0\n"
FOOTER = "EF\n"

PUBTYPE_CODES = {"journal": "J", "book": "B", "book in series": "S", "patent": "P"}

ReferenceFetcher = Callable[[str], Iterable[dict]]


def format_record(rec: dict, references: Iterable[dict] | None = None) -> str:
    """Return one record as tagged plain text, ending with ``ER``.

    ``references`` overrides the cited references embedded in ``rec``, for
    example with the complete list from the ``/references`` endpoint.
    """
    static = rec.get("static_data") or {}
    summary = static.get("summary") or {}
    full = static.get("fullrecord_metadata") or {}
    pub = summary.get("pub_info") or {}
    page = pub.get("page") or {}
    row = flatten(rec)

    titles = {t.get("type"): t.get("content") for t in _list(_get(summary, "titles", "title"))}
    names = _list(_get(summary, "names", "name"))
    authors = sorted((n for n in names if n.get("role") == "author"),
                     key=lambda n: _int(n.get("seq_no")) or 0)
    ids = {i.get("type"): i.get("value") for i in
           _list(_get(rec, "dynamic_data", "cluster_related", "identifiers", "identifier"))}
    refs = list(references) if references is not None else _list(_get(full, "references", "reference"))

    fields: list[tuple[str, str | list[str] | None]] = [
        ("PT", PUBTYPE_CODES.get(str(pub.get("pubtype", "")).lower(), "J")),
        ("AU", [_short_name(a) for a in authors]),
        ("AF", [_full_name(a) for a in authors]),
        ("TI", row["title"]),
        ("SO", row["source"]),
        ("LA", _language(full)),
        ("DT", row["doctype"]),
        ("DE", row["author_keywords"]),
        ("ID", row["keywords_plus"]),
        ("AB", row["abstract"]),
        ("C1", _addresses(full, authors)),
        ("RP", _reprint(full)),
        ("CR", [format_cited_ref(r) for r in refs]),
        ("NR", _str(row["reference_count"] if row["reference_count"] is not None else len(refs))),
        ("TC", _str(row["times_cited"])),
        # Z9 is "times cited, all databases"; only the Core Collection count is
        # reliably available, so it is repeated here for tools that read Z9.
        ("Z9", _str(row["times_cited"])),
        ("SN", row["issn"]),
        ("EI", row["eissn"]),
        ("J9", titles.get("abbrev_29")),
        ("JI", titles.get("abbrev_iso")),
        ("PD", pub.get("pubmonth")),
        ("PY", _str(row["year"])),
        ("VL", row["volume"]),
        ("IS", row["issue"]),
        ("BP", page.get("begin")),
        ("EP", page.get("end")),
        ("AR", ids.get("art_no")),
        ("DI", row["doi"]),
        ("WC", row["wos_categories"]),
        ("SC", row["research_areas"]),
        ("UT", row["uid"]),
    ]

    lines = []
    for tag, value in fields:
        values = [_clean(v) for v in (value if isinstance(value, list) else [value])]
        values = [v for v in values if v]
        if values:
            lines.append(f"{tag} {values[0]}")
            lines.extend(f"   {v}" for v in values[1:])
    lines.append("ER")
    return "\n".join(lines) + "\n"


def write_wos_plaintext(records: Iterable[dict], out: TextIO, *,
                        fetch_references: ReferenceFetcher | None = None) -> int:
    """Write ``records`` to an open text file and return the number written.

    If ``fetch_references`` is given (e.g. ``client.references``), it is called
    for every record whose embedded cited references are fewer than its
    reference count, so the CR field is complete. That costs extra requests.
    """
    out.write(HEADER)
    n = 0
    for rec in records:
        refs = None
        if fetch_references is not None and _references_incomplete(rec):
            refs = list(fetch_references(rec["UID"]))
        out.write("\n" + format_record(rec, refs))
        n += 1
    out.write("\n" + FOOTER)
    return n


def format_cited_ref(ref: dict) -> str:
    """Format a cited reference the way WoS writes it in the CR field:
    ``Johanson J, 1977, J INT BUS STUD, V8, P23, DOI 10.1057/palgrave.jibs.8490676``.

    Accepts both the embedded record shape (``citedAuthor``) and the
    ``/references`` endpoint shape (``CitedAuthor``).
    """
    def g(key: str) -> str | None:
        v = ref.get(key) or ref.get(key[0].upper() + key[1:]) or ref.get(key.upper())
        return _clean(_text(v)) or None

    author = g("citedAuthor")
    parts = [author.replace(",", "") if author else "[Anonymous]"]
    if year := g("year"):
        parts.append(year)
    if work := g("citedWork"):
        parts.append(work)
    if volume := g("volume"):
        parts.append(f"V{volume}")
    if page := g("page"):
        parts.append(f"P{page}")
    if doi := g("doi"):
        parts.append(f"DOI {doi}")
    return ", ".join(parts)


# ---------------------------------------------------------------- helpers


def _references_incomplete(rec: dict) -> bool:
    refs = _get(rec, "static_data", "fullrecord_metadata", "references")
    expected = _int(_get(rec, "static_data", "fullrecord_metadata", "refs", "count"))
    if expected is None:
        expected = _int(refs.get("count")) if isinstance(refs, dict) else None
    embedded = len(_list(_get(refs, "reference")))
    return expected is not None and embedded < expected


def _short_name(n: dict) -> str:
    return n.get("wos_standard") or n.get("display_name") or n.get("full_name") or ""


def _full_name(n: dict) -> str:
    return n.get("full_name") or n.get("display_name") or n.get("wos_standard") or ""


def _language(full: dict) -> str | None:
    langs = _list(_get(full, "languages", "language"))
    primary = [l for l in langs if isinstance(l, dict) and l.get("type") == "primary"]
    return _text((primary or langs)[0]) if langs else None


def _addresses(full: dict, authors: list[dict]) -> list[str]:
    """C1 lines: ``[Author, Full; Other, Name] Full address.``"""
    lines = []
    for entry in _list(_get(full, "addresses", "address_name")):
        spec = entry.get("address_spec") or {}
        address = _text(spec.get("full_address"))
        if not address:
            continue
        people = _list(_get(entry, "names", "name"))
        if not people:  # fall back to the author list's addr_no links
            no = str(spec.get("addr_no", ""))
            people = [a for a in authors if no and no in str(a.get("addr_no", "")).split()]
        prefix = f"[{'; '.join(_full_name(p) for p in people)}] " if people else ""
        lines.append(prefix + address.rstrip(".") + ".")
    return lines


def _reprint(full: dict) -> str | None:
    entries = _list(_get(full, "reprint_addresses", "address_name"))
    if not entries:
        return None
    entry = entries[0]
    people = _list(_get(entry, "names", "name"))
    address = _text((entry.get("address_spec") or {}).get("full_address")).rstrip(".")
    who = "; ".join(f"{_short_name(p)} (corresponding author)" for p in people)
    return ", ".join(x for x in (who, address) if x) + "." if (who or address) else None


def _str(x) -> str | None:
    return None if x is None else str(x)


def _clean(s) -> str:
    """Collapse whitespace so a value can never break the line-based format."""
    return re.sub(r"\s+", " ", str(s)).strip() if s is not None else ""
