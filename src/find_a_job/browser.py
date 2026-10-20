"""Headed Chromium fetch session for the dashboard browser integration.

Contract
--------
``BrowserSession(data_dir)`` lazily starts a *headed*, visible Playwright
Chromium (``sync_api``) on the first :meth:`fetch` and keeps that window open
until :meth:`close` runs on the owning thread. There is no headless fallback:
without a display, launch fails as :class:`BrowserUnavailableError`.

All browser traffic -- the navigation itself, every redirect hop and every
subresource -- passes through one request gate that resolves DNS and rejects
non-public destinations (loopback, private, link-local, reserved, multicast,
unspecified) and non-web schemes. Validation happens before the browser
connects; see the DNS-rebinding note on :meth:`_assert_public_url`.

``fetch(url, artifact_dir)`` returns::

    {
        "url": str,             # caller-supplied URL
        "final_url": str,       # URL of the loaded document
        "status": int | None,   # main-document HTTP status
        "title": str,
        "text": str,            # rendered, visible document text
        "links": [{"url": str, "text": str}, ...],  # visible, http(s), deduped
        "screenshot": str,      # <artifact_dir>/browser.png (absolute)
        "text_artifact": str,   # <artifact_dir>/page.txt (absolute)
        "artifacts": [{"name": str, "media_type": str}, ...],  # same two files
        "browser_visible": True,
        "fetched_at": str,      # UTC ISO-8601, seconds precision
        "redirects": [str, ...],# request chain, oldest first, final last
        "blocked": [{"url": str, "reason": str}, ...],
    }

``artifacts`` uses the names the backend registers verbatim: ``browser.png``
(``image/png``) and ``page.txt`` (``text/plain``), both written into
``artifact_dir``.

Cancellation/ownership: this class holds no task state; the backend owns the
run state machine. It only guarantees idempotent, zombie-free teardown.
Playwright's sync objects are thread-affine, so construction, ``fetch`` and
``close`` must all happen on the same dedicated thread. ``close`` may be
called from any thread only when the browser was never started.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Scheme allow-list for subresources. The main document must be http(s).
NON_NETWORK_SCHEMES = frozenset({"data", "blob", "about"})
WEB_SCHEMES = frozenset({"http", "https"})

NAVIGATION_TIMEOUT_MS = 45_000
ACTION_TIMEOUT_MS = 15_000
SETTLE_TIMEOUT_MS = 10_000
SCREENSHOT_NAME = "browser.png"
TEXT_NAME = "page.txt"

_VISIBLE_LINKS_JS = """
(anchors) => {
  const seen = new Set();
  const out = [];
  for (const anchor of anchors) {
    const style = window.getComputedStyle(anchor);
    const rect = anchor.getBoundingClientRect();
    const visible = style.visibility !== 'hidden'
      && style.display !== 'none'
      && rect.width > 0 && rect.height > 0;
    const url = anchor.href;
    if (!visible || !url || seen.has(url)) continue;
    seen.add(url);
    out.push({
      url,
      text: (anchor.innerText || anchor.textContent || '').replace(/\\s+/g, ' ').trim(),
    });
  }
  return out;
}
"""

_VISIBLE_TEXT_JS = """
() => {
  const root = document.body || document.documentElement;
  return root ? root.innerText || '' : '';
}
"""


class BrowserError(RuntimeError):
    """Base class for browser-session failures."""


class BrowserUnavailableError(BrowserError):
    """Playwright is missing, or headed Chromium cannot launch (no display)."""


class BrowserSessionClosedError(BrowserError):
    """The session was closed and cannot serve another fetch."""


class BrowserThreadError(BrowserError):
    """Session used from a thread other than the one that created it."""


class UnsafeUrlError(BrowserError):
    """URL is not a public http(s) destination and was not loaded."""


class BrowserNavigationError(BrowserError):
    """Navigation failed for a reason other than a blocked destination."""


class BrowserExtractionError(BrowserError):
    """Visible text, links or screenshot could not be captured."""


class BrowserSession:
    """One visible Chromium window reused across fetches on one thread."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir).expanduser().resolve()
        self._owner_thread = threading.get_ident()
        self._playwright = None
        self._context = None
        self._page = None
        self._blocked: list[dict[str, str]] = []
        self._validated_hosts: set[tuple[str, int]] = set()
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return not self._closed

    def close(self) -> None:
        """Close the browser and stop Playwright. Safe to call more than once.

        Teardown is best-effort: a context that is already gone must not raise
        during backend shutdown, and the Playwright driver is always stopped.
        """
        if self._closed:
            return
        if self._context is None and self._playwright is None:
            self._closed = True
            return
        self._assert_owner_thread()
        self._closed = True
        context, playwright = self._context, self._playwright
        self._context = self._page = self._playwright = None
        self._quiet_close(context)
        self._quiet_stop(playwright)

    @staticmethod
    def _quiet_close(handle: Any) -> None:
        if handle is None:
            return
        try:
            handle.close()
        except Exception:
            pass

    @staticmethod
    def _quiet_stop(playwright: Any) -> None:
        if playwright is None:
            return
        try:
            playwright.stop()
        except Exception:
            pass

    # -- public fetch ------------------------------------------------------

    def fetch(self, url: str, artifact_dir: Path) -> dict:
        """Load ``url`` in the visible browser and extract page evidence."""
        self._assert_open()
        self._assert_owner_thread()
        self._assert_public_url(url)
        out_dir = Path(artifact_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_started()
        self._blocked = []
        response = self._navigate(url)
        page = self._page
        page.bring_to_front()
        self._settle_page(page)
        text = self._read_visible_text(page)
        links = self._read_links(page)
        screenshot_path = self._capture_screenshot(page, out_dir)
        text_path = out_dir / TEXT_NAME
        try:
            text_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise BrowserExtractionError(f"could not write {text_path}: {exc}") from exc
        return {
            "url": url,
            "final_url": page.url,
            "status": response.status if response is not None else None,
            "title": self._read_title(page),
            "text": text,
            "links": links,
            "screenshot": str(screenshot_path),
            "text_artifact": str(text_path),
            "artifacts": [
                {"name": SCREENSHOT_NAME, "media_type": "image/png"},
                {"name": TEXT_NAME, "media_type": "text/plain"},
            ],
            "browser_visible": True,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "redirects": self._redirect_chain(response, page.url),
            "blocked": list(self._blocked),
        }

    # -- guards ------------------------------------------------------------

    def _assert_open(self) -> None:
        if self._closed:
            raise BrowserSessionClosedError("browser session is closed")

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise BrowserThreadError(
                "BrowserSession is bound to its creating thread; "
                "run construction, fetch and close on one dedicated thread"
            )

    @staticmethod
    def _is_public_address(address: str) -> bool:
        try:
            parsed = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return False
        return parsed.is_global

    def _resolve_host(self, host: str, port: int) -> None:
        """Refuse hosts that resolve to any non-public address."""
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeUrlError(f"cannot resolve host {host!r}: {exc}") from exc
        addresses = {info[4][0] for info in infos}
        if not addresses:
            raise UnsafeUrlError(f"host {host!r} resolved to no addresses")
        for address in sorted(addresses):
            if not self._is_public_address(address):
                raise UnsafeUrlError(
                    f"host {host!r} resolves to non-public address {address}"
                )

    @staticmethod
    def _web_target(url: str) -> tuple[str, int]:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in WEB_SCHEMES:
            raise UnsafeUrlError(f"scheme {scheme or '<none>'!r} is not http(s)")
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            raise UnsafeUrlError(f"URL {url!r} has no host")
        if parts.username is not None or parts.password is not None:
            raise UnsafeUrlError("URLs with embedded credentials are refused")
        return host, BrowserSession._target_port(parts, scheme)

    @staticmethod
    def _target_port(parts, scheme: str) -> int:
        try:
            return parts.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise UnsafeUrlError(f"URL {parts.geturl()!r} has an invalid port") from exc

    def _assert_public_url(self, url: str) -> None:
        """Reject non-web, credentialed and non-public destinations.

        DNS is resolved here, before Chromium receives the URL. Chromium may
        re-resolve at connect time, so this gate narrows rather than
        eliminates rebinding races; it is not a substitute for network egress
        policy.
        """
        host, port = self._web_target(url)
        key = (host, port)
        if key in self._validated_hosts:
            return
        self._resolve_host(host, port)
        self._validated_hosts.add(key)

    def _route(self, route) -> None:
        """Gate every browser request, including redirect hops and assets."""
        url = route.request.url
        scheme = urlsplit(url).scheme.lower()
        if scheme in NON_NETWORK_SCHEMES:
            route.continue_()
            return
        try:
            self._assert_public_url(url)
        except UnsafeUrlError as exc:
            self._blocked.append({"url": url, "reason": str(exc)})
            route.abort("blockedbyclient")
            return
        except Exception as exc:  # never leave Playwright waiting on a route
            self._blocked.append({"url": url, "reason": f"validation error: {exc}"})
            route.abort("failed")
            return
        route.continue_()

    # -- launch ------------------------------------------------------------

    def _ensure_started(self) -> None:
        if self._context is not None:
            try:
                self._ensure_live_page()
                return
            except Exception:
                # Window closed by the user: discard and relaunch below.
                self._discard_browser()
        self._launch()

    def _launch(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailableError(
                "Python Playwright is not installed; install the 'playwright' "
                "package and its Chromium browser before using the browser stage"
            ) from exc
        profile_dir = self._data_dir / "browser" / "chromium-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                viewport={"width": 1280, "height": 900},
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-networking",
                    "--disable-component-update",
                ],
            )
        except Exception as exc:
            self._discard_browser()
            raise BrowserUnavailableError(
                "headed Chromium could not start; the browser stage requires a "
                f"visible display (DISPLAY/WAYLAND_DISPLAY): {exc}"
            ) from exc
        self._context.set_default_timeout(ACTION_TIMEOUT_MS)
        self._context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        self._context.route("**/*", self._route)
        self._ensure_live_page()

    def _ensure_live_page(self) -> None:
        page = self._page
        if page is not None and not page.is_closed():
            return
        open_pages = [p for p in self._context.pages if not p.is_closed()]
        self._page = open_pages[0] if open_pages else self._context.new_page()

    def _discard_browser(self) -> None:
        context, playwright = self._context, self._playwright
        self._context = self._page = self._playwright = None
        self._quiet_close(context)
        self._quiet_stop(playwright)

    # -- navigation and extraction ----------------------------------------

    def _navigate(self, url: str):
        page = self._page
        try:
            return page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            if self._blocked:
                target = self._blocked[-1]
                raise UnsafeUrlError(
                    f"navigation to {url!r} was blocked: {target['reason']}"
                ) from exc
            raise BrowserNavigationError(
                f"navigation to {url!r} failed: {exc}"
            ) from exc

    @staticmethod
    def _settle_page(page: Any) -> None:
        """Give late-rendering content a bounded chance without failing fetch."""
        try:
            page.wait_for_load_state("load", timeout=SETTLE_TIMEOUT_MS)
        except Exception:
            pass

    @staticmethod
    def _redirect_chain(response, final_url: str) -> list[str]:
        chain: list[str] = []
        request = response.request if response is not None else None
        while request is not None:
            chain.append(request.url)
            request = request.redirected_from
        chain.reverse()
        if not chain:
            chain.append(final_url)
        return chain

    @staticmethod
    def _read_visible_text(page) -> str:
        try:
            return page.evaluate(_VISIBLE_TEXT_JS) or ""
        except Exception as exc:
            raise BrowserExtractionError(f"visible text extraction failed: {exc}") from exc

    @staticmethod
    def _read_links(page) -> list[dict[str, str]]:
        try:
            raw = page.eval_on_selector_all("a[href]", _VISIBLE_LINKS_JS)
        except Exception as exc:
            raise BrowserExtractionError(f"link extraction failed: {exc}") from exc
        return [
            {"url": item["url"], "text": item["text"]}
            for item in raw
            if urlsplit(item.get("url", "")).scheme.lower() in WEB_SCHEMES
        ]

    @staticmethod
    def _read_title(page) -> str:
        try:
            return page.title()
        except Exception:
            return ""

    @staticmethod
    def _capture_screenshot(page, artifact_dir: Path) -> Path:
        path = artifact_dir / SCREENSHOT_NAME
        try:
            page.screenshot(path=str(path), full_page=True)
        except Exception as exc:
            raise BrowserExtractionError(
                f"screenshot capture failed for {path}: {exc}"
            ) from exc
        return path
