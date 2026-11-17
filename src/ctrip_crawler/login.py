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
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import BrowserContext, Page

logger = logging.getLogger(__name__)

# Definitive authentication cookies issued by Ctrip upon successful sign-in.
# Tracking / analytics cookies (_bfa, GUID, UBT_VID, _pd, MKT_CKID, st_sid,
# st_pvi, etc.) that are also issued to anonymous visitors are excluded.
AUTH_COOKIE_NAMES = (
    "cticket",
    "ticket_passport",
    "loginfo",
    "ppinfo",
    "CookieService",
    "ibulc",
    "ibulca",
    "DUID",
    "login_uid",
)

# Page URLs used for sign-in checks and for the interactive sign-in flow.
HOME_URL = "https://you.ctrip.com/"
PASSPORT_LOGIN_URL = "https://passport.ctrip.com/user/login"

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
    '[class*="header_nick"]',
    '[class*="nickname"]',
    '[class*="header_user"]',
    '[class*="user_name"]',
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
    "消息",
    "我的收藏",
    "优惠券",
    "钱包",
)

# --- Interactive sign-in ---------------------------------------------------

# The header button that opens the sign-in dialog.
LOGIN_BUTTON_SELECTORS = (
    '[class*="header_login_title"]',
    '[class*="header_login_not"]',
    'button[class*="header_login"]',
    'a:has-text("登录")',
)

# The sign-in dialog / container wrappers.
LOGIN_DIALOG_SELECTORS = (
    ".lg_loginwrap",
    ".lg_loginbox",
    "#bbz_accounts_pc_lg_box",
    ".lg_loginbox_qrcode",
    ".lg_loginbox_qrcode_wrapper",
)

# Tabs / buttons offered to switch to QR code login.
QR_TAB_SELECTORS = (
    "text=扫码登录",
    ".lg_ercode",
    "text=扫码",
    '[data-testid="qrTab"]',
    'a:has-text("扫码登录")',
)

# QR expiration and auto-refresh selectors.
QR_REFRESH_SELECTORS = (
    "text=点击刷新",
    "text=刷新二维码",
    "text=二维码已失效",
    ".qrcode-refresh",
    ".qrcode_expired",
    ".lg_qrcode_refresh",
)


@dataclass
class LoginState:
    """Result of a login check."""

    is_logged_in: bool
    username: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.is_logged_in


async def check_cookies(context: BrowserContext) -> LoginState:
    """
    Check the session cookies only, without touching the page.

    This is the fast path: when the browser already holds a Ctrip sign-in
    cookie there is no reason to render a page, open a dialog, or wait for
    the user to do anything.

    :param context: the browser context holding the cookies
    :return: a :class:`LoginState` describing what was found
    """
    cookies = await _safe_cookies(context)
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
        detail="no Ctrip sign-in cookie found",
    )


async def check_login_state(page: Page, settle_ms: int = 2500) -> LoginState:
    """
    Inspect the current browser session for a signed-in Ctrip account.

    :param page: the page whose context holds the session cookies
    :param settle_ms: how long to let the page header render before reading it
    :return: a :class:`LoginState` describing what was found
    """
    # 0. Fast path: an existing sign-in cookie means there is nothing to do.
    cookie_state = await check_cookies(page.context)
    if cookie_state.is_logged_in:
        return cookie_state

    # 1. If currently on the passport login page, definitely not logged in yet.
    if "passport.ctrip.com" in (page.url or ""):
        return LoginState(
            is_logged_in=False,
            username="",
            detail="currently on the passport login page",
        )

    # Let the header render so the account bar is present.
    if settle_ms > 0:
        try:
            await asyncio.sleep(settle_ms / 1000)
        except Exception:
            pass

    # 2. The sign-in button is the authoritative "signed out" signal.
    if await has_logged_out_marker(page):
        return LoginState(
            is_logged_in=False,
            username="",
            detail="the header shows the sign-in button",
        )

    # 3. No sign-in button: look for the nickname in the account bar.
    username = await _detect_username(page)
    if username:
        return LoginState(
            is_logged_in=True,
            username=username,
            detail=f"account nickname found in the header: {username}",
        )

    # 4. No nickname either: fall back to the session cookies.
    return cookie_state


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
    domain = (cookie.get("domain") or "").lower()
    if not (domain.endswith("ctrip.com") or domain.endswith("trip.com")):
        return False
    value = (cookie.get("value") or "").strip()
    if not value or value in ('""', "''", "deleted"):
        return False
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
    if any(token in stripped for token in ("我的订单", "联系客服", "我的收藏")):
        return True
    return False


async def has_logged_out_marker(page: Page) -> bool:
    """Return True when the page still shows the sign-in / register button."""
    if "passport.ctrip.com" in (page.url or ""):
        return True
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


async def open_login_dialog(page: Page) -> bool:
    """
    Open Ctrip's sign-in dialog or passport login page in the browser.

    Attempts to click the header "登录" button first. If the button is
    missing, slow to respond, or obscured, falls back to direct navigation to
    Ctrip's official passport login page with BackUrl set to preserve the
    user's target URL.

    :param page: the page to operate on
    :return: True when the sign-in dialog / page is ready for interaction
    """
    current_url = page.url or ""

    # 1. Already on the passport login page?
    if "passport.ctrip.com" in current_url:
        logger.info("Browser is already on the passport login page.")
        return True

    # 2. Make sure we are on a Ctrip page first.
    if "ctrip.com" not in current_url:
        logger.info("Opening the Ctrip homepage to reach the sign-in flow...")
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.5)
        except Exception as exc:
            logger.warning("Could not open the Ctrip homepage: %s", exc)

    # 3. Try clicking the header sign-in button.
    clicked = False
    for selector in LOGIN_BUTTON_SELECTORS:
        try:
            btn = page.locator(selector).first
            if await btn.count() and await btn.is_visible():
                await btn.scroll_into_view_if_needed()
                await btn.click()
                clicked = True
                logger.info("Clicked the sign-in button in the page header.")
                break
        except Exception as exc:
            logger.debug("Could not click header sign-in button (%s): %s", selector, exc)

    # 4. Check if clicking triggered modal display or page redirect.
    if clicked:
        for _ in range(6):
            await asyncio.sleep(0.5)
            if await _login_dialog_visible(page):
                return True

    # 5. Fallback: navigate directly to passport.ctrip.com with BackUrl.
    target_back = page.url if ("ctrip.com" in (page.url or "") and "passport.ctrip.com" not in (page.url or "")) else HOME_URL
    passport_target = f"{PASSPORT_LOGIN_URL}?BackUrl={quote(target_back)}"
    logger.info("Navigating directly to Ctrip sign-in page: %s", passport_target)
    try:
        await page.goto(passport_target, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1.0)
    except Exception as exc:
        logger.warning("Direct navigation to passport page failed: %s", exc)

    # 6. Confirm the dialog / login box is now visible.
    for _ in range(10):
        if await _login_dialog_visible(page):
            return True
        await asyncio.sleep(0.5)
    return False


async def _login_dialog_visible(page: Page) -> bool:
    """Check whether the sign-in dialog or passport login box is visible."""
    if "passport.ctrip.com" in (page.url or ""):
        return True
    for selector in LOGIN_DIALOG_SELECTORS:
        try:
            loc = page.locator(selector).first
            if await loc.count() and await loc.is_visible():
                return True
        except Exception:
            continue
    return False


async def select_qr_login_tab(page: Page) -> bool:
    """
    Switch the sign-in dialog to the QR code tab.

    The QR code is the simplest option to complete — the user just scans it
    with the Ctrip mobile app.

    :param page: the page holding the dialog
    :return: True when the QR tab / code is active
    """
    # Wait for the login box DOM to attach if on the passport page
    if "passport.ctrip.com" in (page.url or ""):
        try:
            await page.wait_for_selector(".lg_loginbox, #bbz_accounts_pc_lg_box", timeout=8000)
        except Exception:
            pass

    # Check if the QR code is already visible
    for qr_sel in (".lg_loginbox_qrcode", ".lg_loginbox_qrcode_wrapper", '[data-testid="qrCodeBox"]'):
        try:
            loc = page.locator(qr_sel).first
            if await loc.count() and await loc.is_visible():
                logger.info("QR code login is already active.")
                return True
        except Exception:
            pass

    # Click the QR login tab switch
    for selector in QR_TAB_SELECTORS:
        try:
            tab = page.locator(selector).first
            if await tab.count() and await tab.is_visible():
                await tab.click()
                await asyncio.sleep(0.8)
                logger.info("Switched to the QR code login tab.")
                return True
        except Exception as exc:
            logger.debug("Could not select QR tab (%s): %s", selector, exc)
    return False


async def save_and_open_qr_code(
    page: Page,
    output_path: Path | str | None = None,
) -> Path | None:
    """
    Capture the login QR code card and open it with the system image viewer.

    This enables scanning even when running in headless mode or when the browser
    window is hidden/backgrounded.

    :param page: the page holding the QR code
    :param output_path: where to save the image (default: output/login_qr.png)
    :return: path of the saved image file, or None on failure
    """
    from .paths import OUTPUT_DIR

    if not output_path:
        target_path = OUTPUT_DIR / "login_qr.png"
    else:
        target_path = Path(output_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Preferred containers in priority order
    selectors = (
        ".lg_loginbox",
        ".lg_loginbox_qrcode_wrapper",
        ".lg_loginbox_qrcode",
        '[data-testid="qrCodeBox"]',
        "#bbz_accounts_pc_lg_box",
    )

    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await loc.count() and await loc.is_visible():
                await loc.screenshot(path=str(target_path))
                logger.info("Login QR code saved to: %s", target_path)
                _open_with_system_viewer(target_path)
                return target_path
        except Exception as exc:
            logger.debug("Could not capture QR container %s: %s", selector, exc)

    return None


def _open_with_system_viewer(file_path: Path) -> None:
    """Open an image using the operating system's default image viewer."""
    try:
        if sys.platform == "win32":
            # os.startfile opens with default Windows photo viewer asynchronously
            os.startfile(str(file_path))
            logger.info("Opened QR code via Windows default image viewer.")
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("Opened QR code via macOS Preview.")
        else:
            subprocess.Popen(["xdg-open", str(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("Opened QR code via default system viewer.")
    except Exception as exc:
        logger.warning("Could not launch default image viewer: %s", exc)


async def wait_for_login(
    page: Page,
    timeout: int = 300,
    poll_interval: float = 2.0,
    popup_qr: bool = True,
) -> LoginState:
    """
    Wait for the user to sign in, polling the session until it turns active.

    On the first attempt this opens Ctrip's real sign-in dialog in the
    browser (QR code / SMS code / password tabs), switches to the QR code,
    and optionally pops up the QR code image using the system image viewer.

    :param page: the page used to poll the session
    :param timeout: maximum number of seconds to wait
    :param poll_interval: seconds between polls
    :param popup_qr: whether to screenshot and pop up the QR image
    :return: the final login state
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    attempt = 0

    while True:
        attempt += 1
        state = await check_login_state(page, settle_ms=1000)
        if state.is_logged_in:
            logger.info("Sign-in detected after %d check(s): %s", attempt, state.detail)
            return state

        remaining = int(deadline - loop.time())
        if remaining <= 0:
            logger.warning(
                "Timed out after %ds waiting for sign-in. Continuing anonymously; "
                "some data may be restricted.",
                timeout,
            )
            return state

        if attempt == 1:
            logger.info(
                "Waiting up to %ds for you to sign in to Ctrip in the browser window.",
                timeout,
            )
            # Open the real sign-in dialog so the user has something to act on.
            if await open_login_dialog(page):
                await select_qr_login_tab(page)
                if popup_qr:
                    qr_file = await save_and_open_qr_code(page)
                    if qr_file:
                        logger.info(
                            "QR code image popped up in your system photo viewer. "
                            "Scan it directly with the Ctrip mobile app!"
                        )
                logger.info(
                    "The Ctrip sign-in dialog is now open in the browser. "
                    "Scan the QR code with the Ctrip app (or use SMS / password), "
                    "then the crawler will resume automatically."
                )
            else:
                logger.warning(
                    "Could not open the sign-in dialog automatically. "
                    "Please sign in manually in the browser window."
                )

        # Detect QR code expiration and auto-click refresh if visible
        for refresh_sel in QR_REFRESH_SELECTORS:
            try:
                refresh_btn = page.locator(refresh_sel).first
                if await refresh_btn.count() and await refresh_btn.is_visible():
                    logger.info("QR code expired, clicking refresh to generate a new QR code...")
                    await refresh_btn.click()
                    await asyncio.sleep(1.0)
                    if popup_qr:
                        await save_and_open_qr_code(page)
                    break
            except Exception:
                pass

        logger.info(
            "Waiting for sign-in... (%ds remaining, poll %d)",
            remaining,
            attempt,
        )
        await asyncio.sleep(poll_interval)


async def ensure_login(
    page: Page,
    require_login: bool = False,
    wait_timeout: int = 0,
    popup_qr: bool = True,
) -> LoginState:
    """
    Ensure the browser session is signed in before crawling.

    The cookie check comes first: when the browser already holds a Ctrip
    sign-in cookie the whole login flow is skipped — no page render, no
    sign-in dialog, no waiting.

    :param page: the page used to check the session
    :param require_login: abort the run when no session is found
    :param wait_timeout: seconds to wait for a sign-in to complete (0 = don't wait)
    :param popup_qr: whether to screenshot and pop up the QR code image
    :return: the resulting login state
    """
    # Fast path: an existing sign-in cookie short-circuits everything.
    cookie_state = await check_cookies(page.context)
    if cookie_state.is_logged_in:
        logger.info(
            "Ctrip sign-in cookie found (%s). Skipping the login flow.",
            cookie_state.detail,
        )
        return cookie_state

    state = await check_login_state(page)

    if state.is_logged_in:
        who = state.username or "existing session"
        logger.info("Signed in to Ctrip (%s). Authenticated requests enabled.", who)
        return state

    logger.warning("Not signed in to Ctrip (%s). The crawler will run anonymously.", state.detail)

    if wait_timeout > 0:
        state = await wait_for_login(page, timeout=wait_timeout, popup_qr=popup_qr)

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
