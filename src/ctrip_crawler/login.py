"""
Ctrip login state detection.

The crawler drives the user's real browser over CDP, so it inherits whatever
session that browser already has. This module inspects that session to tell
whether the user is signed in, and (optionally) waits for a sign-in to
complete.

Detection uses two independent signals:

1. **DOM (primary)** — the site header renders a "登录" (sign in) and "注册"
   (register) button when signed out. Once signed in those buttons disappear
   and the account bar shows the account nickname instead. The sign-in button
   is therefore the authoritative signal.
2. **Cookies (secondary)** — Ctrip issues an auth cookie set on sign-in. Note
   that some cookies (`_bfa`, `GUID`, `_pd`, `UBT_VID`) are also issued to
   anonymous visitors, so they are deliberately excluded from the auth list.

Either signal on its own is enough to conclude "signed in"; both missing means
"anonymous".
"""

import asyncio
import logging
from dataclasses import dataclass

from playwright.async_api import BrowserContext, Page

logger = logging.getLogger(__name__)

# Cookie names Ctrip sets only after a successful sign-in.
# Cookies that are also issued to anonymous visitors (_bfa, GUID, UBT_VID,
# _pd, MKT_CKID, ...) are intentionally excluded.
AUTH_COOKIE_NAMES = (
    "cticket",
    "uuid",
    "sessionid",
    "st_sid",
    "st_pvi",
    "loginfo",
    "ppinfo",
    "CookieService",
    "ibulc",
    "ibulca",
    "cvid",
    "cvinfo",
)

# Page URLs used for sign-in checks and for the interactive sign-in flow.
HOME_URL = "https://you.ctrip.com/"

# The header account bar. Present in both states, so it is only a container.
ACCOUNT_BAR_SELECTORS = (
    '[id="hp_nfes_accountbar"]',
    '[class*="header_account"]',
)

# Selectors that only exist when signed OUT (the sign-in / register buttons).
LOGGED_OUT_SELECTORS = (
    '[class*="header_login_title"]',
    '[class*="header_login_not"]',
    '[class*="header_register"]',
    '[class*="header_login_wrapper"]',
)

# Selectors that may hold the account nickname when signed IN.
NICKNAME_SELECTORS = (
    '[class*="header_user_name"]',
    '[class*="user_nickname"]',
    '[class*="nickname"]',
    '[class*="header_user"]',
)

# Text that appears on the sign-in button when signed out.
LOGGED_OUT_TEXTS = ("登录", "注册", "sign in", "log in", "login", "register")

# Menu entries in the account bar that are present regardless of sign-in state.
# They must never be mistaken for a nickname.
ACCOUNT_MENU_TEXTS = (
    "我的订单",
    "联系客服",
    "订单",
    "客服",
    "退出",
    "登录",
    "注册",
)


@dataclass
class LoginState:
    """Result of a login check."""

    is_logged_in: bool
    username: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.is_logged_in


async def check_login_state(page: Page, settle_ms: int = 2500) -> LoginState:
    """
    Inspect the current browser session for a signed-in Ctrip account.

    :param page: the page whose context holds the session cookies
    :param settle_ms: how long to let the page header render before reading it
    :return: a :class:`LoginState` describing what was found
    """
    # Let the header render so the account bar is present.
    if settle_ms > 0:
        try:
            await asyncio.sleep(settle_ms / 1000)
        except Exception:
            pass

    # 1. The sign-in button is the authoritative "signed out" signal.
    if await has_logged_out_marker(page):
        return LoginState(
            is_logged_in=False,
            username="",
            detail="the header shows the sign-in button",
        )

    # 2. No sign-in button: look for the nickname in the account bar.
    username = await _detect_username(page)
    if username:
        return LoginState(
            is_logged_in=True,
            username=username,
            detail=f"account nickname found in the header: {username}",
        )

    # 3. No nickname either: fall back to the session cookies.
    cookies = await _safe_cookies(page.context)
    auth_cookies = [c for c in cookies if _is_auth_cookie(c)]
    if auth_cookies:
        names = ", ".join(sorted({c["name"] for c in auth_cookies}))
        return LoginState(
            is_logged_in=True,
            username="",
            detail=f"session cookies found: {names}",
        )

    return LoginState(
        is_logged_in=False,
        username="",
        detail="no account nickname and no session cookies found",
    )


async def _safe_cookies(context: BrowserContext) -> list[dict]:
    """Read context cookies, tolerating a closed or restricted browser."""
    try:
        cookies = await context.cookies()
    except Exception as exc:
        logger.debug("Could not read cookies: %s", exc)
        return []
    return [
        {"name": c.get("name", ""), "value": c.get("value", ""), "domain": c.get("domain", "")}
        for c in cookies
    ]


def _is_auth_cookie(cookie: dict) -> bool:
    """Decide whether a cookie indicates an authenticated session."""
    name = (cookie.get("name") or "").lower()
    return name in {n.lower() for n in AUTH_COOKIE_NAMES}


async def _detect_username(page: Page) -> str:
    """
    Look for the account nickname rendered in the page header.

    The account bar also holds generic menu entries ("我的订单", "联系客服",
    ...) that are present whether or not the user is signed in, so those are
    filtered out before the text is accepted as a nickname.
    """
    for selector in NICKNAME_SELECTORS:
        try:
            loc = page.locator(selector).first
            if not await loc.count():
                continue
            text = (await loc.inner_text()).strip()
            text = " ".join(text.split())
            if not text or len(text) > 40:
                continue
            if _looks_like_menu_entry(text):
                continue
            return text
        except Exception:
            continue
    return ""


def _looks_like_menu_entry(text: str) -> bool:
    """Reject generic account-bar menu entries that are not a nickname."""
    stripped = text.strip()
    for token in ACCOUNT_MENU_TEXTS:
        if stripped == token:
            return True
    # A menu entry embedded in a longer string (e.g. "登录 注册 我的订单")
    # is not a nickname either.
    if any(token in stripped for token in ("我的订单", "联系客服")):
        return True
    return False


async def has_logged_out_marker(page: Page) -> bool:
    """Return True when the page still shows the sign-in / register button."""
    for selector in LOGGED_OUT_SELECTORS:
        try:
            loc = page.locator(selector).first
            if await loc.count():
                text = (await loc.inner_text()).strip()
                if any(token.lower() in text.lower() for token in LOGGED_OUT_TEXTS):
                    return True
        except Exception:
            continue
    return False


async def wait_for_login(
    page: Page,
    timeout: int = 300,
    poll_interval: float = 2.0,
) -> LoginState:
    """
    Wait for the user to sign in, polling the session until it turns active.

    The browser window stays in front, so the user can complete the sign-in
    (including any QR code or SMS step) in their own browser while this
    coroutine polls.

    :param page: the page used to poll the session
    :param timeout: maximum number of seconds to wait
    :param poll_interval: seconds between polls
    :return: the final login state
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    attempt = 0

    while True:
        attempt += 1
        state = await check_login_state(page, settle_ms=1200)
        if state.is_logged_in:
            logger.info("Sign-in detected after %d check(s): %s", attempt, state.detail)
            return state

        remaining = deadline - loop.time()
        if remaining <= 0:
            logger.warning(
                "Timed out after %ds waiting for sign-in. Continuing anonymously; "
                "some data may be restricted.",
                timeout,
            )
            return state

        if attempt == 1:
            logger.info(
                "Waiting up to %ds for you to sign in to Ctrip in the browser window "
                "(complete any QR code or SMS step there).",
                timeout,
            )
        logger.info(
            "Still not signed in. Retrying in %s (%d/%d)...",
            f"{poll_interval:g}s",
            attempt,
            max(1, int(timeout / poll_interval)),
        )
        await asyncio.sleep(poll_interval)


async def ensure_login(
    page: Page,
    require_login: bool = False,
    wait_timeout: int = 0,
) -> LoginState:
    """
    Ensure the browser session is signed in before crawling.

    :param page: the page used to check the session
    :param require_login: abort the run when no session is found
    :param wait_timeout: seconds to wait for a sign-in to complete (0 = don't wait)
    :return: the resulting login state
    """
    state = await check_login_state(page)

    if state.is_logged_in:
        who = state.username or "existing session"
        logger.info("Signed in to Ctrip (%s). Authenticated requests enabled.", who)
        return state

    logger.warning("Not signed in to Ctrip (%s). The crawler will run anonymously.", state.detail)

    if wait_timeout > 0:
        state = await wait_for_login(page, timeout=wait_timeout)

    if require_login and not state.is_logged_in:
        raise RuntimeError(
            "A signed-in Ctrip session is required but none was detected "
            f"({state.detail}). Sign in with the browser on the debugging port, "
            "then retry."
        )

    return state


def summarize(state: LoginState) -> str:
    """One-line English summary of a login state, for the terminal banner."""
    if state.is_logged_in:
        who = state.username or "existing session"
        return f"Logged in ({who})"
    return "Not logged in (anonymous mode)"
