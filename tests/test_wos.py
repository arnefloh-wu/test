import io
import json
import re

import pytest

from wos_client import WosClient, WosError, flatten, to_dataframe
from wos_client.cli import main


def make_rec(i, *, single=False):
    """A minimal REC in the API's JSON shape. ``single`` uses dicts instead of
    one-element lists, as the API does when an element occurs once."""
    authors = [
        {"role": "author", "seq_no": 2, "wos_standard": "Doe, J"},
        {"role": "author", "seq_no": 1, "wos_standard": "Smith, A"},
        {"role": "book_editor", "seq_no": 1, "wos_standard": "Ed, X"},
    ]
    addr = {"address_spec": {"country": "Austria", "organizations": {"organization": [
        {"content": "WU Vienna"}, {"pref": "Y", "content": "Vienna University of Economics & Business"}]}}}
    keyword = "internationalization"
    return {
        "UID": f"WOS:{i:015d}",
        "static_data": {
            "summary": {
                "titles": {"title": [{"type": "source", "content": "JOURNAL OF INTERNATIONAL BUSINESS STUDIES"},
                                     {"type": "item", "content": f"Paper {i}"}]},
                "names": {"name": authors[1] if single else authors},
                "pub_info": {"pubyear": 2020, "vol": "51", "issue": "3",
                             "page": {"begin": "10", "end": "30", "content": "10-30"}},
                "doctypes": {"doctype": "Article"},
            },
            "fullrecord_metadata": {
                "addresses": {"address_name": addr if single else [addr, addr]},
                "keywords": {"keyword": keyword if single else [keyword, "SMEs"]},
                "category_info": {"subjects": {"subject": [
                    {"ascatype": "traditional", "content": "Business"},
                    {"ascatype": "traditional", "content": "Management"},
                    {"ascatype": "extended", "content": "Business & Economics"}]}},
                "abstracts": {"abstract": {"abstract_text": {"p": ["First.", "Second."]}}},
                "refs": {"count": 85},
            },
            "item": {"keywords_plus": {"keyword": ["PERFORMANCE", "FIRMS"]}},
        },
        "dynamic_data": {
            "citation_related": {"tc_list": {"silo_tc": [
                {"coll_id": "WOS", "local_count": 42}, {"coll_id": "MEDLINE", "local_count": 1}]}},
            "cluster_related": {"identifiers": {"identifier": [
                {"type": "issn", "value": "0047-2506"}, {"type": "doi", "value": f"10.1057/{i}"}]}},
        },
    }


def page(recs, found, query_id=7):
    return {"QueryResult": {"QueryID": query_id, "RecordsSearched": 1, "RecordsFound": found},
            "Data": {"Records": {"records": {"REC": recs} if recs else ""}}}


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = {"X-REC-AmtPerYear-Remaining": "4000", **(headers or {})}
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.responses.pop(0)


def client(responses):
    s = FakeSession(responses)
    return WosClient("key", session=s, requests_per_second=0), s


def test_flatten_full_record():
    row = flatten(make_rec(1))
    assert row["title"] == "Paper 1"
    assert row["source"] == "JOURNAL OF INTERNATIONAL BUSINESS STUDIES"
    assert row["authors"] == "Smith, A; Doe, J"  # sorted by seq_no, editors dropped
    assert row["author_count"] == 2
    assert row["doi"] == "10.1057/1"
    assert row["times_cited"] == 42
    assert row["countries"] == "Austria"
    assert row["organizations"] == "Vienna University of Economics & Business"
    assert row["wos_categories"] == "Business; Management"
    assert row["research_areas"] == "Business & Economics"
    assert row["keywords_plus"] == "PERFORMANCE; FIRMS"
    assert row["abstract"] == "First. Second."
    assert row["pages"] == "10-30"
    assert row["year"] == 2020
    assert row["reference_count"] == 85


def test_flatten_single_elements_and_empty_record():
    row = flatten(make_rec(2, single=True))
    assert row["authors"] == "Smith, A"
    assert row["author_keywords"] == "internationalization"
    empty = flatten({"UID": "WOS:1"})
    assert empty["uid"] == "WOS:1" and empty["authors"] == "" and empty["times_cited"] is None


def test_search_paginates_with_query_id():
    c, s = client([
        FakeResponse(200, page([make_rec(i) for i in range(100)], 150)),
        FakeResponse(200, page([make_rec(i) for i in range(100, 150)], 150)),
    ])
    recs = list(c.search('TS=("psychic distance")'))
    assert len(recs) == 150
    assert s.calls[0][1]["usrQuery"] == 'TS=("psychic distance")'
    assert s.calls[1][0].endswith("/query/7")
    assert s.calls[1][1]["firstRecord"] == 101
    assert c.records_remaining == 4000


def test_search_respects_max_records():
    c, s = client([FakeResponse(200, page([make_rec(i) for i in range(5)], 500))])
    assert len(list(c.search("TS=x", max_records=5))) == 5
    assert s.calls[0][1]["count"] == 5 and len(s.calls) == 1


def test_empty_result():
    c, _ = client([FakeResponse(200, page([], 0))])
    assert list(c.search("TS=nothing")) == []


def test_single_record_result_is_not_a_list():
    c, _ = client([FakeResponse(200, page(make_rec(1), 1))])
    assert [r["UID"] for r in c.search("TS=x")] == ["WOS:000000000000001"]


def test_retries_on_429_then_raises_on_4xx(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c, _ = client([FakeResponse(429, {}), FakeResponse(200, page([], 0))])
    assert c.count("TS=x") == 0
    c, _ = client([FakeResponse(400, {"message": "Invalid query"})])
    with pytest.raises(WosError, match="Invalid query"):
        c.count("TS=(")


def test_missing_key(monkeypatch):
    monkeypatch.delenv("WOS_API_KEY", raising=False)
    with pytest.raises(ValueError):
        WosClient()


def test_to_dataframe():
    df = to_dataframe([make_rec(1), make_rec(2)])
    assert list(df["times_cited"]) == [42, 42]
    assert df.shape == (2, 22)


def test_cli_writes_csv(tmp_path, monkeypatch):
    s = FakeSession([FakeResponse(200, page([make_rec(1), make_rec(2)], 2))])
    monkeypatch.setattr("requests.Session", lambda: s)
    out = tmp_path / "out.csv"
    assert main(["--api-key", "k", "search", "TS=x", "-o", str(out)]) == 0
    lines = out.read_text().splitlines()
    assert lines[0].startswith("uid,doi,title") and len(lines) == 3


# ------------------------------------------------------------ WoS plain text

from wos_client.export import format_cited_ref, format_record, write_wos_plaintext  # noqa: E402


def make_full_rec(i=1):
    rec = make_rec(i)
    full = rec["static_data"]["fullrecord_metadata"]
    summary = rec["static_data"]["summary"]
    summary["pub_info"]["pubtype"] = "Journal"
    summary["pub_info"]["pubmonth"] = "APR"
    summary["titles"]["title"].append({"type": "abbrev_29", "content": "J INT BUS STUD"})
    for n in summary["names"]["name"]:
        n["full_name"] = {"Smith, A": "Smith, Alice", "Doe, J": "Doe, John"}.get(n["wos_standard"])
        n["addr_no"] = "1"
    full["languages"] = {"language": {"type": "primary", "content": "English"}}
    full["addresses"] = {"address_name": [
        {"address_spec": {"addr_no": 1, "full_address": "Vienna Univ Econ & Business, Vienna, Austria",
                          "country": "Austria"},
         "names": {"name": [{"full_name": "Smith, Alice"}, {"full_name": "Doe, John"}]}},
        {"address_spec": {"addr_no": 2, "full_address": "Univ Leeds, Leeds, England"}},  # no names
    ]}
    full["reprint_addresses"] = {"address_name": {
        "address_spec": {"full_address": "Vienna Univ Econ & Business, Vienna, Austria."},
        "names": {"name": {"wos_standard": "Smith, A"}}}}
    full["refs"] = {"count": 2}
    full["references"] = {"count": 2, "reference": [
        {"uid": "WOS:A1977DH95600002", "citedAuthor": "Johanson, J", "year": "1977",
         "citedWork": "J INT BUS STUD", "volume": "8", "page": "23",
         "doi": "10.1057/palgrave.jibs.8490676"},
        {"citedWork": "WORLD INVESTMENT REP", "year": "2020"},
    ]}
    return rec


def test_format_cited_ref_both_shapes():
    assert format_cited_ref({"citedAuthor": "Johanson, J", "year": "1977", "citedWork": "J INT BUS STUD",
                             "volume": "8", "page": "23", "doi": "10.1057/x"}) \
        == "Johanson J, 1977, J INT BUS STUD, V8, P23, DOI 10.1057/x"
    # /references endpoint uses capitalised keys
    assert format_cited_ref({"CitedAuthor": "Hymer, S", "Year": "1976", "CitedWork": "INT OPERATIONS NATL"}) \
        == "Hymer S, 1976, INT OPERATIONS NATL"
    assert format_cited_ref({"year": "2020", "citedWork": "X"}) == "[Anonymous], 2020, X"


def test_format_record_tags_and_continuations():
    text = format_record(make_full_rec())
    lines = text.splitlines()
    assert lines[0] == "PT J" and lines[-1] == "ER"
    assert "AU Smith, A\n   Doe, J\nAF Smith, Alice\n   Doe, John" in text
    assert "C1 [Smith, Alice; Doe, John] Vienna Univ Econ & Business, Vienna, Austria.\n" \
           "   Univ Leeds, Leeds, England." in text
    assert "RP Smith, A (corresponding author), Vienna Univ Econ & Business, Vienna, Austria." in lines
    assert "CR Johanson J, 1977, J INT BUS STUD, V8, P23, DOI 10.1057/palgrave.jibs.8490676\n" \
           "   [Anonymous], 2020, WORLD INVESTMENT REP" in text
    for expected in ["LA English", "DT Article", "DE internationalization; SMEs", "TC 42", "NR 2",
                     "J9 J INT BUS STUD", "PD APR", "PY 2020", "BP 10", "EP 30", "DI 10.1057/1",
                     "WC Business; Management", "SC Business & Economics", "UT WOS:000000000000001"]:
        assert expected in lines
    # every line is a tag line or a continuation, so the format cannot be broken by newlines
    assert all(re.match(r"^([A-Z][A-Z0-9] |   |ER$)", line) for line in lines)


def test_newlines_in_values_are_collapsed():
    rec = make_full_rec()
    rec["static_data"]["fullrecord_metadata"]["abstracts"]["abstract"]["abstract_text"]["p"] = ["a\nb", "c"]
    assert "AB a b c" in format_record(rec).splitlines()


def test_write_file_and_fetch_missing_references():
    rec = make_full_rec()
    rec["static_data"]["fullrecord_metadata"]["refs"]["count"] = 3  # embedded list is incomplete
    fetched = []

    def fetch(uid):
        fetched.append(uid)
        return [{"CitedAuthor": "A, B", "Year": "2000"}] * 3

    out = io.StringIO()
    assert write_wos_plaintext([rec, make_rec(2)], out, fetch_references=fetch) == 2
    text = out.getvalue()
    assert text.startswith("﻿FN Clarivate Analytics Web of Science\nVR 1.0\n")
    assert text.endswith("ER\n\nEF\n") and text.count("\nER\n") == 2
    # both records report more references than they embed (make_rec has 85, none embedded)
    assert fetched == ["WOS:000000000000001", "WOS:000000000000002"]
    assert text.count("A B, 2000") == 6
    # a complete embedded list costs no request
    fetched.clear()
    write_wos_plaintext([make_full_rec()], io.StringIO(), fetch_references=fetch)
    assert fetched == []


def test_cli_txt_and_convert(tmp_path, monkeypatch):
    s = FakeSession([FakeResponse(200, page([make_full_rec(1), make_full_rec(2)], 2))])
    monkeypatch.setattr("requests.Session", lambda: s)
    raw = tmp_path / "raw.jsonl"
    assert main(["--api-key", "k", "search", "TS=x", "-o", str(raw), "--raw"]) == 0
    out = tmp_path / "savedrecs.txt"
    assert main(["convert", str(raw), "-o", str(out)]) == 0  # no API key or request needed
    text = out.read_text(encoding="utf-8-sig")
    assert text.count("\nPT J\n") == 2 and "CR Johanson J, 1977" in text


def test_base_url_default_env_and_override(monkeypatch):
    monkeypatch.delenv("WOS_API_URL", raising=False)
    c, s = client([FakeResponse(200, page([], 0))])
    c.count("TS=x")
    assert s.calls[0][0] == "https://api.clarivate.com/api/wos/"
    monkeypatch.setenv("WOS_API_URL", "https://wos-api.clarivate.com/api/wos/")
    assert WosClient("k").base_url == "https://wos-api.clarivate.com/api/wos"
    assert WosClient("k", base_url="https://x/api/wos").base_url == "https://x/api/wos"
