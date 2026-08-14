"""Kusto query client.

Records what the fleet does not: how long a query took and how many rows it
returned. `Invoke-KustoQuery` in the fleet keeps only the result rows, which is
why nobody could ever size the cost of a query it ran.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .auth import TokenProvider


class KustoError(RuntimeError):
    """A query failed. The service's own message is preserved."""


@dataclass
class QueryResult:
    rows: list[dict[str, Any]]
    duration_ms: int
    row_count: int
    query: str
    cluster: str
    database: str
    truncated: bool = False
    columns: list[str] = field(default_factory=list)


class KustoClient:
    def __init__(self, tokens: TokenProvider, timeout: int = 120) -> None:
        self._tokens = tokens
        self._timeout = timeout

    def query(
        self,
        cluster: str,
        database: str,
        query: str,
        auth_resource: str | None = None,
        max_rows: int | None = None,
    ) -> QueryResult:
        resource = auth_resource or cluster
        started = time.perf_counter()
        payload = self._post(cluster, database, query, resource, retry_on_auth=True)
        duration_ms = int((time.perf_counter() - started) * 1000)

        table = _primary_table(payload)
        columns = [c.get("ColumnName", "") for c in table.get("Columns", [])]
        raw_rows = table.get("Rows", []) or []

        truncated = False
        if max_rows is not None and len(raw_rows) > max_rows:
            raw_rows = raw_rows[:max_rows]
            truncated = True

        rows = [dict(zip(columns, row)) for row in raw_rows]
        return QueryResult(
            rows=rows,
            duration_ms=duration_ms,
            row_count=len(rows),
            query=query,
            cluster=cluster,
            database=database,
            truncated=truncated,
            columns=columns,
        )

    def _post(
        self,
        cluster: str,
        database: str,
        query: str,
        resource: str,
        retry_on_auth: bool,
    ) -> dict:
        token = self._tokens.token(resource)
        try:
            response = httpx.post(
                f"{cluster.rstrip('/')}/v1/rest/query",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={"db": database, "csl": query},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise KustoError(
                f"Query against {cluster}/{database} timed out after {self._timeout}s. "
                "Narrow the window, or raise OCE_SENTRY_QUERY_TIMEOUT."
            ) from exc
        except httpx.HTTPError as exc:
            raise KustoError(f"Could not reach {cluster}: {exc}") from exc

        if response.status_code in (401, 403) and retry_on_auth:
            # A cached token can be rejected after a tenant or account change.
            # Re-acquire once before deciding this is an entitlement problem.
            self._tokens.invalidate(resource)
            return self._post(cluster, database, query, resource, retry_on_auth=False)

        if response.status_code >= 400:
            raise KustoError(_describe_failure(response, cluster, database))

        try:
            return response.json()
        except ValueError as exc:
            raise KustoError(f"{cluster} returned a non-JSON response ({response.status_code}).") from exc


def _describe_failure(response: httpx.Response, cluster: str, database: str) -> str:
    detail = ""
    try:
        body = response.json()
        error = body.get("error") or {}
        detail = error.get("@message") or error.get("message") or ""
        inner = error.get("@innererror") or {}
        if not detail and isinstance(inner, dict):
            detail = inner.get("message", "")
    except ValueError:
        detail = (response.text or "").strip()[:500]

    if response.status_code in (401, 403):
        return (
            f"Not authorised for {cluster}/{database} ({response.status_code}). "
            f"The signed-in identity may lack access. {detail}".strip()
        )
    return f"Query against {cluster}/{database} failed ({response.status_code}). {detail}".strip()


def _primary_table(payload: dict) -> dict:
    """Return the result table.

    v1 responses carry several tables; the results are Table_0. Ordering is not
    guaranteed to be stable, so select by name and fall back to position.
    """
    tables = payload.get("Tables")
    if not tables:
        raise KustoError("Kusto response contained no tables.")
    for table in tables:
        if table.get("TableName") == "Table_0":
            return table
    return tables[0]
