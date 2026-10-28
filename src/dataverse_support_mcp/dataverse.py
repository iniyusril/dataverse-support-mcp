"""Thin Dataverse Web API client: client-credentials token, retries, error mapping."""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable
from urllib.parse import quote

import httpx

from .config import Settings

_RETRY_STATUSES = {429, 502, 503, 504}
_MAX_ATTEMPTS = 3
_QUERY_SAFE = "(),'/:*"


class DataverseError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Dataverse error {status}: {message}")
        self.status = status
        self.message = message


def _error_message(resp: httpx.Response) -> str:
    try:
        message = resp.json()["error"]["message"]
    except Exception:  # not the usual OData error envelope
        message = resp.text or resp.reason_phrase
    return str(message).strip()[:500]


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    raw = resp.headers.get("Retry-After")
    try:
        return min(float(raw), 15.0) if raw else attempt * 1.5
    except ValueError:
        return attempt * 1.5


class DataverseClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.Client | None = None,
        token_provider: Callable[[], str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._http = http or httpx.Client(timeout=30.0)
        self._token_provider = token_provider or self._acquire_token
        self._sleep = sleep
        self._msal_app = None

    # -- auth -----------------------------------------------------------------
    def _acquire_token(self) -> str:
        if self._msal_app is None:
            import msal

            self._msal_app = msal.ConfidentialClientApplication(
                self.settings.client_id,
                authority=f"https://login.microsoftonline.com/{self.settings.tenant_id}",
                client_credential=self.settings.client_secret,
            )
        # msal serves this from its in-memory cache until shortly before expiry.
        result = self._msal_app.acquire_token_for_client(scopes=[self.settings.scope])
        token = result.get("access_token")
        if not token:
            reason = (result.get("error_description") or result.get("error") or "unknown error")
            raise DataverseError(401, "Could not get an access token: " + reason.splitlines()[0])
        return token

    # -- http -----------------------------------------------------------------
    def _url(self, path: str, params: dict[str, Any] | None) -> str:
        url = path if path.startswith("https://") else f"{self.settings.api_base}/{path.lstrip('/')}"
        if params:
            # Spaces must be %20 (not '+') for OData $filter.
            query = "&".join(
                f"{key}={quote(str(value), safe=_QUERY_SAFE)}"
                for key, value in params.items()
                if value is not None
            )
            url += ("&" if "?" in url else "?") + query
        return url

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        prefer: Iterable[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = self._url(path, params)
        prefer_parts = list(prefer or [])
        resp: httpx.Response | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            request_headers = {
                "Authorization": f"Bearer {self._token_provider()}",
                "Accept": "application/json",
                "OData-MaxVersion": "4.0",
                "OData-Version": "4.0",
                **(headers or {}),
            }
            if prefer_parts:
                request_headers["Prefer"] = ",".join(prefer_parts)
            resp = self._http.request(method, url, headers=request_headers, json=json)
            if resp.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
                self._sleep(_retry_delay(resp, attempt))
                continue
            break
        assert resp is not None
        if resp.status_code >= 400:
            raise DataverseError(resp.status_code, _error_message(resp))
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def get(self, path: str, *, params: dict[str, Any] | None = None, prefer: Iterable[str] | None = None) -> dict[str, Any]:
        return self.request("GET", path, params=params, prefer=prefer)

    def post(self, path: str, json: Any, *, prefer: Iterable[str] | None = None) -> dict[str, Any]:
        return self.request("POST", path, json=json, prefer=prefer)

    def patch(self, path: str, json: Any) -> dict[str, Any]:
        # If-Match: * makes PATCH update-only; without it Dataverse would upsert
        # (create a new row) when the id does not exist.
        return self.request("PATCH", path, json=json, headers={"If-Match": "*"})
