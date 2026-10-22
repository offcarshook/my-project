"""HTTP helper with retries for the Alpaca REST APIs."""
import logging
import time

import requests

log = logging.getLogger(__name__)
RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status
        self.message = message


def request_json(method: str, url: str, headers: dict, params: dict | None = None, json: dict | None = None,
                 retries: int = 5, timeout: float = 15, backoff: float = 1.0, session=None):
    """Send a request and return the decoded JSON body (None for empty bodies).

    Retries rate limits (429, honoring Retry-After), server errors and connection problems
    with exponential backoff; other 4xx errors raise ApiError straight away."""
    http = session or requests
    for attempt in range(retries + 1):
        try:
            resp = http.request(method, url, headers=headers, params=params, json=json, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == retries:
                raise ApiError(None, f"connection failed: {exc}") from exc
            wait = backoff * 2 ** attempt
            log.warning("connection problem (%s), retrying in %.0fs", exc, wait)
            time.sleep(wait)
            continue
        if resp.status_code in RETRY_STATUS and attempt < retries:
            wait = float(resp.headers.get("Retry-After") or backoff * 2 ** attempt)
            log.warning("HTTP %s from %s, retrying in %.0fs", resp.status_code, url, wait)
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            try:
                message = resp.json().get("message", resp.text)
            except ValueError:
                message = resp.text
            raise ApiError(resp.status_code, message)
        return resp.json() if resp.content else None
    raise ApiError(None, "retries exhausted")
