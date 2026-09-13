from __future__ import annotations

import asyncio
from typing import Any

import httpx


class SyncASGIClient:
    """Synchronous test adapter backed by modern httpx ASGITransport."""

    def __init__(
        self,
        app: Any,
        *,
        base_url: str = "http://testserver",
        follow_redirects: bool = True,
    ) -> None:
        self._app = app
        self._base_url = base_url
        self._follow_redirects = follow_redirects
        self._cookies = httpx.Cookies()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(
                app=self._app,
                raise_app_exceptions=True,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url=self._base_url,
                follow_redirects=self._follow_redirects,
                cookies=self._cookies,
            ) as client:
                response = await client.request(method, url, **kwargs)
                self._cookies.update(response.cookies)
                return response

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(send())

        raise RuntimeError(
            "SyncASGIClient cannot run inside an active event loop. "
            "Use httpx.AsyncClient with ASGITransport in async tests."
        )

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)
