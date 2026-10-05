import json

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
