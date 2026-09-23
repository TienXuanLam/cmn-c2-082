"""HTTP primitives for bounded runbook retrieval."""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise urllib.error.HTTPError(req.full_url, code, "Redirects are disabled", headers, fp)
