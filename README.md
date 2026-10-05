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

## VOSviewer and Bibliometrix

An output file ending in `.txt` (or `-f wos`) is written in the Web of Science
**plain-text "Full record and cited references"** export format, the same
`savedrecs.txt` you would download from the WoS website:

```bash
wos search 'TS=("psychic distance") AND PY=2000-2025' --edition WOS+SSCI -o savedrecs.txt

# Already have raw records? Convert them without using any API quota:
wos convert jibs2024.jsonl -o savedrecs.txt
```

* **VOSviewer:** Create map → *Create a map based on bibliographic data* →
  *Read data from bibliographic database files* → *Web of Science*, then pick
  the file. Co-authorship, co-occurrence, citation, bibliographic coupling and
  co-citation maps all work.
* **Bibliometrix / biblioshiny:**
  ```r
  library(bibliometrix)
  M <- convert2df("savedrecs.txt", dbsource = "wos", format = "plaintext")
  results <- biblioAnalysis(M)
  ```

Fields written: `PT AU AF TI SO LA DT DE ID AB C1 RP CR NR TC Z9 SN EI J9 JI
PD PY VL IS BP EP AR DI WC SC UT`. Cited references (`CR`) use the WoS form
`Johanson J, 1977, J INT BUS STUD, V8, P23, DOI 10.1057/...`, which drives
co-citation, bibliographic coupling and local citation analyses.

Notes:

* **Cited references:** if a full record embeds fewer references than its
  reference count, `--fetch-references` fills `CR` from the `/references`
  endpoint. This costs one extra request per affected record.
* **`Z9` (times cited, all databases):** the API's Core Collection count is
  used here, the same value as `TC`.
* **Testing:** the format was checked against bibliometrix's own
  `convert2df` (AU_UN, AU_CO, AU1_CO, CR_SO, `biblioAnalysis` and
  `localCitations` all came out correctly) and the independent `wosfile`
  parser. VOSviewer reads this standard format, but it was not run in testing.

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

# WoS plain-text export for VOSviewer / Bibliometrix
from wos_client import write_wos_plaintext
with open("savedrecs.txt", "w", encoding="utf-8") as f:
    write_wos_plaintext(records, f, fetch_references=wos.references)
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
