"""Bounded, same-origin fetcher.

A ``Fetcher`` serves one source for one run. It only requests URLs on the
source's own hosts, honours robots.txt, waits between requests, stops at the
configured request and byte limits, and stores every body in the raw cache
(when the source's rights allow private storage), sending conditional requests
so unchanged pages cost one 304.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from gleanyard.cache import CachedResponse, RawCache, RunManifest, Validators
from gleanyard.config import SourceConfig
from gleanyard.errors import Blocked, BoundsExceeded, TransportError

MAX_REDIRECTS = 5
RETRY_STATUS = frozenset({500, 502, 503, 504})
BLOCK_STATUS = frozenset({401, 403, 429, 451})


class Response(CachedResponse):
    body: bytes
    from_cache: bool = False

    @property
    def text(self) -> str:
        return self.body.decode(_charset(self.content_type), errors="replace")


def _charset(content_type: str | None) -> str:
    for part in (content_type or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name.lower() == "charset" and value:
            return value.strip("\"'")
    return "utf-8"


def allowed_hosts(source: SourceConfig) -> frozenset[str]:
    hosts = {urlsplit(str(source.url)).hostname}
    if source.homepage is not None:
        hosts.add(urlsplit(str(source.homepage)).hostname)
    return frozenset(host for host in hosts if host)


class Fetcher:
    def __init__(
        self,
        source: SourceConfig,
        *,
        user_agent: str,
        cache: RawCache | None = None,
        client: httpx.Client | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        run_id: str | None = None,
    ) -> None:
        self.source = source
        self.limits = source.fetch
        self.user_agent = user_agent
        # Raw bodies are kept only where the source's rights permit private storage.
        self.cache = cache if source.rights.allowed_actions.private_store else None
        self.client = client or httpx.Client(timeout=self.limits.timeout_seconds)
        self.max_attempts = max_attempts
        self.sleep = sleep
        self.hosts = allowed_hosts(source)
        self.requests = 0
        self._last_request: float | None = None
        self._robots: dict[str, RobotFileParser] = {}
        self._validators = self.cache.validators(source.id) if self.cache else {}
        started = datetime.now(UTC)
        self.manifest = RunManifest(
            source_id=source.id,
            run_id=run_id or started.strftime("%Y%m%dT%H%M%S%fZ"),
            started_at=started,
        )

    def get(self, url: str) -> Response:
        url = self._check_url(url)
        self._check_robots(url)
        validators = self._validators.get(url)
        headers = {"User-Agent": self.user_agent}
        if validators is not None:
            if validators.etag:
                headers["If-None-Match"] = validators.etag
            if validators.last_modified:
                headers["If-Modified-Since"] = validators.last_modified
        response, final_url = self._request(url, headers)
        retrieved_at = datetime.now(UTC)
        content_type = response.headers.get("content-type")
        if response.status_code == 304 and validators is not None and self.cache is not None:
            body = self.cache.get_body(validators.sha256)
            result = Response(
                url=final_url,
                status_code=200,
                content_type=content_type,
                sha256=validators.sha256,
                size=len(body),
                retrieved_at=retrieved_at,
                not_modified=True,
                body=body,
                from_cache=True,
            )
        else:
            body = response.content
            digest = self.cache.put_body(body) if self.cache else _sha256(body)
            result = Response(
                url=final_url,
                status_code=response.status_code,
                content_type=content_type,
                sha256=digest,
                size=len(body),
                retrieved_at=retrieved_at,
                body=body,
            )
            if self.cache is not None:
                self._validators[url] = Validators(
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                    sha256=digest,
                )
        self.manifest.responses.append(
            CachedResponse.model_validate(result.model_dump(exclude={"body", "from_cache"}))
        )
        return result

    def finish(self) -> RunManifest:
        """Persist the run manifest and validators; call once after the last request."""
        if self.cache is not None:
            self.cache.save_manifest(self.manifest)
            self.cache.save_validators(self.source.id, self._validators)
        return self.manifest

    def _check_url(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
            raise TransportError(self.source.id, f"not a credential-free http(s) URL: {url}")
        if parts.hostname not in self.hosts:
            raise BoundsExceeded(
                self.source.id, f"{parts.hostname} is not one of the source's hosts"
            )
        return parts._replace(fragment="").geturl()

    def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(origin)
        if parser is None:
            parser = RobotFileParser()
            response, _ = self._request(
                f"{origin}/robots.txt", {"User-Agent": self.user_agent}, robots=True
            )
            if response.status_code in {401, 403}:
                parser.disallow_all = True
            elif response.status_code >= 400:
                parser.allow_all = True
            else:
                parser.parse(response.text.splitlines())
            self._robots[origin] = parser
        if not parser.can_fetch(self.user_agent, url):
            raise Blocked(self.source.id, f"robots.txt disallows {url}")

    def _request(
        self, url: str, headers: dict[str, str], *, robots: bool = False
    ) -> tuple[httpx.Response, str]:
        for _ in range(MAX_REDIRECTS + 1):
            response = self._send(url, headers, robots=robots)
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("location")
            if not location:
                raise TransportError(self.source.id, f"redirect without location from {url}")
            target = urljoin(url, location)
            if robots and urlsplit(target).hostname not in self.hosts:
                return httpx.Response(404), url
            url = self._check_url(target)
        else:
            raise TransportError(self.source.id, f"more than {MAX_REDIRECTS} redirects")
        if robots:
            return response, url
        if response.status_code in BLOCK_STATUS:
            raise Blocked(self.source.id, f"HTTP {response.status_code} from {url}")
        if response.status_code >= 400:
            raise TransportError(self.source.id, f"HTTP {response.status_code} from {url}")
        return response, url

    def _send(self, url: str, headers: dict[str, str], *, robots: bool) -> httpx.Response:
        for attempt in range(1, self.max_attempts + 1):
            self._count_request()
            try:
                response = self._stream(url, headers)
            except httpx.HTTPError as error:
                if attempt == self.max_attempts:
                    raise TransportError(
                        self.source.id, f"{type(error).__name__} for {url}"
                    ) from error
                self.sleep(2**attempt)
                continue
            if response.status_code in RETRY_STATUS and attempt < self.max_attempts and not robots:
                self.sleep(2**attempt)
                continue
            return response
        raise AssertionError("unreachable")

    def _stream(self, url: str, headers: dict[str, str]) -> httpx.Response:
        with self.client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > self.limits.max_response_bytes:
                    raise BoundsExceeded(
                        self.source.id,
                        f"response from {url} exceeds {self.limits.max_response_bytes} bytes",
                    )
            # The body is already decoded, so drop headers that describe the wire form.
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
            }
            return httpx.Response(response.status_code, headers=headers, content=bytes(body))

    def _count_request(self) -> None:
        if self.requests >= self.limits.max_requests:
            raise BoundsExceeded(
                self.source.id, f"reached the limit of {self.limits.max_requests} requests"
            )
        if self._last_request is not None:
            wait = self.limits.delay_seconds - (time.monotonic() - self._last_request)
            if wait > 0:
                self.sleep(wait)
        self.requests += 1
        self._last_request = time.monotonic()


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
