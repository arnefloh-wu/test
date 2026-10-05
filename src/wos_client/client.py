"""HTTP client for the Clarivate Web of Science Expanded API (v1).

Docs: https://developer.clarivate.com/apis/wos
"""

from __future__ import annotations

import os
import time
from typing import Any, Iterator

import requests

BASE_URL = "https://wos-api.clarivate.com/api/wos"
MAX_PAGE_SIZE = 100  # hard limit of the Expanded API per request


class WosError(RuntimeError):
    """Raised when the API returns an error that retries cannot fix."""

    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class WosClient:
    """Thin wrapper around the WoS Expanded API.

    Handles authentication, client-side throttling (the API allows a small
    number of requests per second), retries with backoff on 429/5xx, and
    pagination via the QueryID returned by the first search request.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        database: str = "WOS",
        base_url: str = BASE_URL,
        requests_per_second: float = 2.0,
        max_retries: int = 5,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ):
        self.api_key = api_key or os.environ.get("WOS_API_KEY")
        if not self.api_key:
            raise ValueError("No API key: pass api_key or set WOS_API_KEY.")
        self.database = database
        self.base_url = base_url.rstrip("/")
        self.min_interval = 1.0 / requests_per_second if requests_per_second else 0.0
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"X-ApiKey": self.api_key, "Accept": "application/json"})
        self._last_request = 0.0
        # Remaining yearly quota, updated from response headers.
        self.records_remaining: int | None = None
        self.requests_remaining: int | None = None

    # ------------------------------------------------------------------ core

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            resp = self.session.get(url, params=params, timeout=self.timeout)
            self._update_quota(resp.headers)

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                time.sleep(delay)
                continue
            raise WosError(resp.status_code, _error_message(resp))
        raise AssertionError("unreachable")

    def _update_quota(self, headers: requests.structures.CaseInsensitiveDict) -> None:
        rec = headers.get("X-REC-AmtPerYear-Remaining")
        req = headers.get("X-REQ-ReqPerYear-Remaining")
        if rec is not None and rec.isdigit():
            self.records_remaining = int(rec)
        if req is not None and req.isdigit():
            self.requests_remaining = int(req)

    # -------------------------------------------------------------- endpoints

    def search_page(
        self,
        query: str,
        *,
        count: int = MAX_PAGE_SIZE,
        first_record: int = 1,
        edition: str | None = None,
        publish_time_span: str | None = None,
        sort_field: str | None = None,
        option_view: str = "FR",
    ) -> dict[str, Any]:
        """Run one search request and return the raw JSON response.

        ``query`` uses WoS advanced search syntax, e.g. ``TS=("psychic distance")``.
        ``publish_time_span`` is ``"YYYY-MM-DD+YYYY-MM-DD"``; ``sort_field``
        e.g. ``"PY+D"`` (year, descending) or ``"TC+D"`` (times cited).
        """
        return self._get(
            "/",
            {
                "databaseId": self.database,
                "usrQuery": query,
                "count": count,
                "firstRecord": first_record,
                "edition": edition,
                "publishTimeSpan": publish_time_span,
                "sortField": sort_field,
                "optionView": option_view,
            },
        )

    def query_page(self, query_id: int | str, *, count: int = MAX_PAGE_SIZE, first_record: int = 1,
                   option_view: str = "FR") -> dict[str, Any]:
        """Fetch a further page of an earlier search by its QueryID."""
        return self._get(
            f"/query/{query_id}",
            {"count": count, "firstRecord": first_record, "optionView": option_view},
        )

    def search(self, query: str, *, max_records: int | None = None, **kwargs: Any) -> Iterator[dict]:
        """Yield every raw record (``REC``) matching ``query``, paging automatically.

        Stops after ``max_records`` records if given. Extra keyword arguments
        are passed to :meth:`search_page`.
        """
        option_view = kwargs.get("option_view", "FR")
        first = self.search_page(query, count=_page_size(max_records, 0), **kwargs)
        info = first.get("QueryResult", {})
        total = int(info.get("RecordsFound", 0))
        if max_records is not None:
            total = min(total, max_records)

        yielded = 0
        for rec in _records(first):
            if yielded >= total:
                return
            yield rec
            yielded += 1

        query_id = info.get("QueryID")
        while yielded < total:
            page = self.query_page(
                query_id,
                count=_page_size(total, yielded),
                first_record=yielded + 1,
                option_view=option_view,
            )
            recs = _records(page)
            if not recs:
                return
            for rec in recs:
                if yielded >= total:
                    return
                yield rec
                yielded += 1

    def count(self, query: str, **kwargs: Any) -> int:
        """Return the number of records matching ``query`` (costs one request, ~0 records)."""
        resp = self.search_page(query, count=0, **kwargs)
        return int(resp.get("QueryResult", {}).get("RecordsFound", 0))

    def get_record(self, uid: str, *, option_view: str = "FR") -> dict | None:
        """Fetch a single record by its UT / UID, e.g. ``WOS:000123456700001``."""
        resp = self._get(f"/id/{uid}", {"databaseId": self.database, "count": 1,
                                         "firstRecord": 1, "optionView": option_view})
        recs = _records(resp)
        return recs[0] if recs else None

    def citing(self, uid: str, *, max_records: int | None = None) -> Iterator[dict]:
        """Yield records that cite ``uid``."""
        yield from self._paged_by_uid("citing", uid, max_records)

    def related(self, uid: str, *, max_records: int | None = None) -> Iterator[dict]:
        """Yield records sharing cited references with ``uid``."""
        yield from self._paged_by_uid("related", uid, max_records)

    def references(self, uid: str, *, max_records: int | None = None) -> Iterator[dict]:
        """Yield the cited references of ``uid`` (flat reference dicts, not full records)."""
        yielded, first_record = 0, 1
        while True:
            resp = self._get(
                f"/references/{uid}",
                {"databaseId": self.database, "count": _page_size(max_records, yielded),
                 "firstRecord": first_record},
            )
            refs = _ensure_list(resp.get("Data"))
            total = int(resp.get("QueryResult", {}).get("RecordsFound", 0))
            if max_records is not None:
                total = min(total, max_records)
            for ref in refs:
                if yielded >= total:
                    return
                yield ref
                yielded += 1
            if not refs or yielded >= total:
                return
            first_record = yielded + 1

    def _paged_by_uid(self, kind: str, uid: str, max_records: int | None) -> Iterator[dict]:
        yielded, first_record, total = 0, 1, None
        while total is None or yielded < total:
            resp = self._get(
                f"/{kind}/{uid}",
                {"databaseId": self.database, "count": _page_size(max_records, yielded),
                 "firstRecord": first_record, "optionView": "FR"},
            )
            if total is None:
                total = int(resp.get("QueryResult", {}).get("RecordsFound", 0))
                if max_records is not None:
                    total = min(total, max_records)
            recs = _records(resp)
            if not recs:
                return
            for rec in recs:
                if yielded >= total:
                    return
                yield rec
                yielded += 1
            first_record = yielded + 1


# ---------------------------------------------------------------- helpers


def _page_size(limit: int | None, done: int) -> int:
    if limit is None:
        return MAX_PAGE_SIZE
    return max(1, min(MAX_PAGE_SIZE, limit - done))


def _records(resp: dict) -> list[dict]:
    recs = (((resp.get("Data") or {}).get("Records") or {}).get("records") or {})
    if not isinstance(recs, dict):  # empty result comes back as ""
        return []
    return _ensure_list(recs.get("REC"))


def _ensure_list(x: Any) -> list:
    if x is None or x == "":
        return []
    return x if isinstance(x, list) else [x]


def _error_message(resp: requests.Response) -> str:
    try:
        body = resp.json()
        return str(body.get("message") or body.get("error") or body)
    except ValueError:
        return resp.text[:500]
