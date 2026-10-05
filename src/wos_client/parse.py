"""Flatten raw WoS Expanded API records (``REC``) into analysis-ready rows.

The API's JSON is a direct translation of its XML, so any element can be a
single dict or a list of dicts depending on how many there are. Every lookup
here goes through ``_list`` to cope with that.
"""

from __future__ import annotations

from typing import Any

SEP = "; "

COLUMNS = [
    "uid", "doi", "title", "source", "year", "volume", "issue", "pages",
    "doctype", "authors", "author_count", "countries", "organizations",
    "author_keywords", "keywords_plus", "wos_categories", "research_areas",
    "times_cited", "reference_count", "abstract", "issn", "eissn",
]


def flatten(rec: dict) -> dict[str, Any]:
    """Return a flat dict with one value per column in :data:`COLUMNS`."""
    static = rec.get("static_data") or {}
    summary = static.get("summary") or {}
    full = static.get("fullrecord_metadata") or {}
    dynamic = rec.get("dynamic_data") or {}

    titles = {t.get("type"): t.get("content") for t in _list(_get(summary, "titles", "title"))}
    pub = summary.get("pub_info") or {}
    page = pub.get("page") or {}
    authors = [n for n in _list(_get(summary, "names", "name")) if n.get("role") == "author"]
    authors.sort(key=lambda n: int(n.get("seq_no") or 0))
    addresses = [a.get("address_spec") or {} for a in _list(_get(full, "addresses", "address_name"))]
    ids = {i.get("type"): i.get("value")
           for i in _list(_get(dynamic, "cluster_related", "identifiers", "identifier"))}
    subjects = _list(_get(full, "category_info", "subjects", "subject"))

    return {
        "uid": rec.get("UID"),
        "doi": ids.get("doi") or ids.get("xref_doi"),
        "title": titles.get("item"),
        "source": titles.get("source"),
        "year": _int(pub.get("pubyear")),
        "volume": pub.get("vol"),
        "issue": pub.get("issue"),
        "pages": _pages(page),
        "doctype": SEP.join(_text(d) for d in _list(_get(summary, "doctypes", "doctype"))),
        "authors": SEP.join(a.get("wos_standard") or a.get("display_name") or "" for a in authors),
        "author_count": len(authors),
        "countries": SEP.join(_unique(a.get("country") for a in addresses)),
        "organizations": SEP.join(_unique(_pref_org(a) for a in addresses)),
        "author_keywords": SEP.join(_text(k) for k in _list(_get(full, "keywords", "keyword"))),
        "keywords_plus": SEP.join(
            _text(k) for k in _list(_get(static, "item", "keywords_plus", "keyword"))),
        "wos_categories": SEP.join(
            _text(s) for s in subjects if s.get("ascatype") == "traditional"),
        "research_areas": SEP.join(
            _text(s) for s in subjects if s.get("ascatype") == "extended"),
        "times_cited": _times_cited(dynamic),
        "reference_count": _int((full.get("refs") or {}).get("count")),
        "abstract": _abstract(full),
        "issn": ids.get("issn"),
        "eissn": ids.get("eissn"),
    }


def flatten_all(records) -> list[dict[str, Any]]:
    return [flatten(r) for r in records]


def to_dataframe(records):
    """Return a pandas DataFrame of flattened records (requires pandas)."""
    import pandas as pd

    return pd.DataFrame(flatten_all(records), columns=COLUMNS)


# ---------------------------------------------------------------- helpers


def _get(d: Any, *keys: str) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _list(x: Any) -> list:
    if x is None or x == "":
        return []
    return x if isinstance(x, list) else [x]


def _text(x: Any) -> str:
    if isinstance(x, dict):
        return str(x.get("content", ""))
    return "" if x is None else str(x)


def _int(x: Any) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _unique(items) -> list[str]:
    seen: dict[str, None] = {}
    for i in items:
        if i:
            seen.setdefault(str(i), None)
    return list(seen)


def _pages(page: dict) -> str | None:
    begin, end = page.get("begin"), page.get("end")
    if begin and end and begin != end:
        return f"{begin}-{end}"
    return begin or page.get("content") or None


def _pref_org(address_spec: dict) -> str | None:
    orgs = _list(_get(address_spec, "organizations", "organization"))
    for o in orgs:  # the unified (preferred) name is flagged pref="Y"
        if isinstance(o, dict) and o.get("pref") == "Y":
            return o.get("content")
    return _text(orgs[0]) if orgs else None


def _times_cited(dynamic: dict) -> int | None:
    for silo in _list(_get(dynamic, "citation_related", "tc_list", "silo_tc")):
        if silo.get("coll_id") == "WOS":
            return _int(silo.get("local_count"))
    return None


def _abstract(full: dict) -> str | None:
    abstracts = _list(_get(full, "abstracts", "abstract"))
    if not abstracts:
        return None
    paras = _list(_get(abstracts[0], "abstract_text", "p"))
    return " ".join(_text(p) for p in paras) or None
