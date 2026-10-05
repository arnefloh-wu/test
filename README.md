# wos-client

A small Python client for the **Clarivate Web of Science Expanded API**. It
turns WoS advanced-search queries into flat, analysis-ready tables (CSV,
JSONL or a pandas DataFrame) for bibliometric work such as systematic
literature reviews, citation analysis and co-authorship or keyword networks.

## Install

```bash
pip install -e ".[pandas]"
export WOS_API_KEY=...   # from developer.clarivate.com (your institution's Expanded API key)
```

## Command line

```bash
# How big is the corpus? (costs one request and no records from your quota)
wos count 'TS=("psychic distance" OR "cultural distance") AND PY=2000-2025' --edition WOS+SSCI

# Export it, most-cited first
wos search 'TS=("psychic distance" OR "cultural distance") AND PY=2000-2025' \
    --edition WOS+SSCI --sort TC+D -o distance.csv

# Keep the full raw JSON records for later re-parsing
wos search 'SO=("JOURNAL OF INTERNATIONAL BUSINESS STUDIES") AND PY=2024' -o jibs2024.jsonl --raw
```

## Python

```python
from wos_client import WosClient, to_dataframe

wos = WosClient()  # reads WOS_API_KEY
records = list(wos.search(
    'TS=("born global*" OR "international new venture*")',
    edition="WOS+SSCI", publish_time_span="2010-01-01+2025-12-31",
))
df = to_dataframe(records)
df.groupby("year").size()                                     # publication trend
df.assign(country=df.countries.str.split("; ")).explode("country").country.value_counts()

# Citation network around a seminal paper
uid = df.sort_values("times_cited", ascending=False).uid.iloc[0]
citing = to_dataframe(wos.citing(uid, max_records=500))
refs = list(wos.references(uid))                              # its cited references
print(wos.records_remaining, "records left in this year's quota")
```

### Flattened columns

`uid, doi, title, source, year, volume, issue, pages, doctype, authors,
author_count, countries, organizations, author_keywords, keywords_plus,
wos_categories, research_areas, times_cited, reference_count, abstract,
issn, eissn`

Multi-valued fields are joined with `"; "`. `organizations` uses the unified
(preferred) institution name where WoS provides one. `times_cited` is the
WoS Core Collection count.

## How it works

| Method | Endpoint |
|---|---|
| `search(query, max_records=…)` | `GET /` then `GET /query/{QueryID}` for later pages |
| `count(query)` | `GET /` with `count=0` |
| `get_record(uid)` | `GET /id/{uid}` |
| `citing(uid)` / `related(uid)` | `GET /citing/{uid}`, `GET /related/{uid}` |
| `references(uid)` | `GET /references/{uid}` |

* **Paging:** each request returns up to 100 records, and paging is automatic.
* **Throttling:** requests are spaced to 2 per second by default
  (`requests_per_second=`). On HTTP 429 or 5xx the client retries with
  exponential backoff (`max_retries=5`).
* **Quota:** the remaining yearly record and request quota is read from the
  response headers into `records_remaining` and `requests_remaining`.
* **Single element or list:** the API returns one element as a dict and
  several as a list. The parser handles both.

## Tests

```bash
pip install -e ".[dev]" && pytest
```

The tests use a fake HTTP session, so they need no API key.
