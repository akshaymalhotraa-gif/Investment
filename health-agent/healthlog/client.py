"""Thin HTTP client for the Google Health API v4."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import requests

from . import auth, config


class ApiError(RuntimeError):
    """A non-2xx from the Health API, with the body preserved for diagnosis."""

    def __init__(self, status: int, body: str, method: str, url: str):
        self.status = status
        self.body = body
        super().__init__(f"{method} {url} -> {status}\n{body}")


class HealthClient:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._session = requests.Session()

    # ---- plumbing -------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{config.API_ROOT}{path}"

        if self.dry_run and method != "GET":
            print(f"[dry-run] {method} {url}")
            print(json.dumps(body, indent=2))
            return {"dryRun": True, "method": method, "url": url, "body": body}

        resp = self._session.request(
            method,
            url,
            params=params,
            json=body,
            headers={
                "Authorization": f"Bearer {auth.access_token()}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        if not resp.ok:
            raise ApiError(resp.status_code, resp.text, method, url)
        return resp.json() if resp.content else {}

    @staticmethod
    def _points_path(data_type: str) -> str:
        return f"/users/me/dataTypes/{data_type}/dataPoints"

    # ---- operations -----------------------------------------------------

    def create(self, data_type: str, datapoint: dict[str, Any]) -> dict[str, Any]:
        """Write a DataPoint.

        Writes are asynchronous - the API answers with an Operation rather than
        the stored record, so a success here means accepted, not yet queryable.
        """
        return self._request("POST", self._points_path(data_type), body=datapoint)

    def list(
        self,
        data_type: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        page_size: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"pageSize": page_size}
        # The `food` catalogue is not time-indexed, so a time filter is invalid
        # there rather than merely useless.
        if data_type != config.FOOD_TYPE:
            if start:
                params["startTime"] = start.isoformat()
            if end:
                params["endTime"] = end.isoformat()
        return self._request("GET", self._points_path(data_type), params=params)

    def delete(self, data_type: str, point_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"{self._points_path(data_type)}/{point_id}")

    def patch(
        self, data_type: str, point_id: str, datapoint: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "PATCH", f"{self._points_path(data_type)}/{point_id}", body=datapoint
        )
