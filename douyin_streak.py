#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音自动续火花 v6。

修复点：
- 逐屏扫描虚拟会话列表，避免滚动后好友 DOM 被卸载。
- 通过搜索框定位好友，并在发送前严格校验右侧聊天标题。
- 只在当前聊天输入区点击表情按钮。
- 优先发送“早上好”，找不到时发送名称包含“续火”的表情。
- 在一个浏览器登录会话内，最多使用 3 个独立页面并发处理。
"""

import asyncio
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
import winreg
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from playwright.async_api import (
        BrowserContext,
        Locator,
        Page,
        TimeoutError as PlaywrightTimeout,
        async_playwright,
    )
except ImportError:
    import ctypes

    ctypes.windll.user32.MessageBoxW(
        0,
        "请先安装 Playwright：\npip install playwright",
        "抖音续火花",
        0x10,
    )
    raise SystemExit(1)


ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    path = ROOT / "config.json"
    if path.exists():
        with path.open("r", encoding="utf-8") as file:
            user_config = json.load(file)
    else:
        user_config = {}

    default_config = {
        "douyin": {
            "base_url": "https://www.douyin.com",
            "messages_url": "https://www.douyin.com/chat?isPopup=1",
        },
        "schedule": {"hour": 9, "minute": 0},
        "message": {
            "type": "emoji",
            "emoji_keywords": ["早上好", "续火"],
            "text": "早上好",
        },
        "login": {
            "login_wait_seconds": 180,
        },
        "run_state": {
            "skip_if_success_today": True,
            "file": ".douyin_streak_state.json",
        },
        "parallel": {"enabled": True, "max_concurrent": 3},
        "targets": {"excluded_names": []},
        "behavior": {
            "headless": False,
            "close_browser_after": True,
            "max_retries": 3,
            "action_delay_ms": 800,
            "page_timeout_ms": 30000,
            "timeout_seconds": 600,
        },
        "log": {"file": "douyin_streak.log", "keep_days": 30},
    }

    def merge(base: dict, override: dict) -> dict:
        result = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = merge(result[key], value)
            else:
                result[key] = value
        return result

    return merge(default_config, user_config)


CONFIG = load_config()


def setup_logging() -> None:
    config = CONFIG.get("log", {})
    handler = TimedRotatingFileHandler(
        ROOT / config.get("file", "douyin_streak.log"),
        when="midnight",
        backupCount=int(config.get("keep_days", 30)),
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[handler, logging.StreamHandler(sys.stdout)],
        force=True,
    )


setup_logging()


@dataclass(frozen=True)
class BrowserInfo:
    name: str
    process_name: str
    user_data_dir: str
    executable_candidates: tuple[str, ...]


@dataclass(frozen=True)
class Friend:
    name: str
    streak: str


@dataclass
class Result:
    friend: Friend
    success: bool
    detail: str
    worker_id: int


def get_browser_info() -> BrowserInfo:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations"
            r"\UrlAssociations\http\UserChoice",
        )
        prog_id, _ = winreg.QueryValueEx(key, "Progid")
        winreg.CloseKey(key)
    except Exception:
        prog_id = "ChromeHTML"

    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", "")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")

    if "Edge" in prog_id:
        return BrowserInfo(
            "Edge",
            "msedge.exe",
            os.path.join(local, "Microsoft", "Edge", "User Data"),
            (
                os.path.join(
                    program_files_x86,
                    "Microsoft",
                    "Edge",
                    "Application",
                    "msedge.exe",
                ),
                os.path.join(
                    program_files,
                    "Microsoft",
                    "Edge",
                    "Application",
                    "msedge.exe",
                ),
            ),
        )

    return BrowserInfo(
        "Chrome",
        "chrome.exe",
        os.path.join(local, "Google", "Chrome", "User Data"),
        (
            os.path.join(
                program_files, "Google", "Chrome", "Application", "chrome.exe"
            ),
            os.path.join(
                program_files_x86,
                "Google",
                "Chrome",
                "Application",
                "chrome.exe",
            ),
            os.path.join(
                local, "Google", "Chrome", "Application", "chrome.exe"
            ),
        ),
    )


def find_executable(info: BrowserInfo) -> Optional[str]:
    return next(
        (path for path in info.executable_candidates if os.path.exists(path)),
        None,
    )


def browser_running(process_name: str) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return process_name.lower() in result.stdout.lower()
    except Exception:
        return False


def stop_browser(process_name: str) -> bool:
    logging.info("正在关闭已运行的 %s ...", process_name)
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", process_name],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode not in (0, 128):
            logging.error("关闭浏览器失败：%s", result.stderr.strip())
            return False
        time.sleep(3)
        return True
    except Exception as exc:
        logging.error("关闭浏览器失败：%s", exc)
        return False


async def random_delay(
    low_ms: Optional[int] = None, high_ms: Optional[int] = None
) -> None:
    base = int(CONFIG.get("behavior", {}).get("action_delay_ms", 800))
    low = base // 2 if low_ms is None else low_ms
    high = base * 2 if high_ms is None else high_ms
    await asyncio.sleep(random.uniform(low, high) / 1000)


def safe_filename(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value
    )
    return cleaned[:50] or "unknown"


def normalized_chat_name(value: str) -> str:
    """搜索结果中的群聊名称可能自动附加成员数，如“示例群聊(12)”."""
    compact = re.sub(r"\s+", "", value or "")
    return re.sub(r"(?:\(\d+\)|（\d+）)$", "", compact)


def run_state_path() -> Path:
    filename = str(
        CONFIG.get("run_state", {}).get("file", ".douyin_streak_state.json")
    )
    return ROOT / filename


def skip_if_success_today() -> bool:
    return bool(
        CONFIG.get("run_state", {}).get("skip_if_success_today", True)
    )


def today_key() -> str:
    return datetime.now().date().isoformat()


def already_success_today() -> bool:
    if not skip_if_success_today():
        return False
    path = run_state_path()
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except Exception as exc:
        logging.warning("读取运行状态失败，将继续执行：%s", exc)
        return False
    return state.get("last_success_date") == today_key()


def mark_success_today(detail: str) -> None:
    if not skip_if_success_today():
        return
    state = {
        "last_success_date": today_key(),
        "last_success_at": datetime.now().isoformat(timespec="seconds"),
        "detail": detail,
    }
    try:
        with run_state_path().open("w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
    except Exception as exc:
        logging.warning("写入运行状态失败：%s", exc)


def chat_names_match(actual: str, expected: str) -> bool:
    return normalized_chat_name(actual) == normalized_chat_name(expected)


async def save_failure_screenshot(page: Page, name: str, reason: str) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = (
        f"failure_{timestamp}_{safe_filename(name)}_{safe_filename(reason)}.png"
    )
    try:
        await page.screenshot(path=str(ROOT / filename), full_page=True)
        logging.info("[%s] 已保存失败截图：%s", name, filename)
    except Exception as exc:
        logging.debug("[%s] 保存截图失败：%s", name, exc)


async def chat_list_ready(page: Page, timeout_ms: int = 5000) -> bool:
    try:
        await page.wait_for_selector(
            ".conversationConversationItemwrapper, "
            ".conversationConversationListwrapper, "
            '[data-e2e="conversation-item"]',
            timeout=timeout_ms,
        )
        return True
    except PlaywrightTimeout:
        return False


async def ensure_logged_in(page: Page) -> bool:
    """确认抖音聊天页可用；未登录时给首次使用者一个扫码登录窗口。"""
    chat_url = CONFIG.get("douyin", {}).get(
        "messages_url", "https://www.douyin.com/chat?isPopup=1"
    )
    await page.goto(chat_url, wait_until="domcontentloaded")
    if await chat_list_ready(page):
        logging.info("已检测到抖音登录状态")
        await random_delay(1000, 1800)
        return True

    if bool(CONFIG.get("behavior", {}).get("headless", False)):
        logging.error(
            "未检测到抖音登录状态；当前是静默/无窗口模式，无法扫码登录。"
            "请先把 config.json 里的 behavior.headless 改为 false，"
            "手动运行 python douyin_streak.py 完成登录后，再恢复静默模式。"
        )
        return False

    wait_seconds = int(CONFIG.get("login", {}).get("login_wait_seconds", 180))
    logging.warning(
        "未检测到抖音登录状态。已打开登录页面，请在 %d 秒内完成扫码或验证。",
        wait_seconds,
    )
    base_url = CONFIG.get("douyin", {}).get("base_url", "https://www.douyin.com")
    try:
        await page.goto(base_url, wait_until="domcontentloaded")
    except Exception:
        pass
    try:
        await page.goto(chat_url, wait_until="domcontentloaded")
        await page.wait_for_selector(
            ".conversationConversationItemwrapper, "
            ".conversationConversationListwrapper, "
            '[data-e2e="conversation-item"]',
            timeout=wait_seconds * 1000,
        )
        logging.info("登录成功，继续执行续火花流程")
        await random_delay(1000, 1800)
        return True
    except PlaywrightTimeout:
        logging.error("等待登录超时，脚本结束")
        return False


async def open_chat_page(page: Page) -> None:
    url = CONFIG.get("douyin", {}).get(
        "messages_url", "https://www.douyin.com/chat?isPopup=1"
    )
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_selector(
        ".conversationConversationItemwrapper, "
        ".conversationConversationListwrapper, "
        '[data-e2e="conversation-item"]',
        timeout=20000,
    )
    await random_delay(1000, 1800)


async def collect_visible_streak_friends(page: Page) -> list[Friend]:
    rows = await page.locator('[data-e2e="conversation-item"]').evaluate_all(
        """
        items => items.map(item => {
            const streak = item.querySelector('.commonStreakstreakContainer');
            const title = item.querySelector('.conversationConversationItemtitle');
            const text = streak?.querySelector('.commonStreaknormalText');
            return {
                name: title?.textContent?.trim() || '',
                streak: text?.textContent?.trim() || '火花',
                hasStreak: Boolean(streak)
            };
        }).filter(item => item.hasStreak && item.name)
        """
    )
    return [Friend(row["name"], row["streak"]) for row in rows]


async def scan_all_streak_friends(page: Page) -> list[Friend]:
    """逐屏扫描虚拟列表；每滚一屏就读取一次当前存在的节点。"""
    logging.info("开始逐屏扫描火花好友...")
    # 真正承载滚动条的是 conversationConversationListwrapper。
    # 外层 componentsLeftPanelboxList 的 scrollHeight 等于 clientHeight，
    # 修改它的 scrollTop 不会触发虚拟列表加载。
    list_locator = page.locator(
        ".conversationConversationListwrapper"
    ).first
    await list_locator.wait_for(state="visible")
    await list_locator.evaluate("element => { element.scrollTop = 0; }")
    await random_delay(300, 500)

    found: dict[str, Friend] = {}
    end_confirmations = 0
    previous_position = -1
    maximum_scroll_height = 0

    for _ in range(160):
        for friend in await collect_visible_streak_friends(page):
            found.setdefault(friend.name, friend)

        state = await list_locator.evaluate(
            """
            element => {
                const before = element.scrollTop;
                // 小步滚动，确保虚拟列表每批节点都至少被读取一次。
                const step = Math.max(180, element.clientHeight * 0.4);
                element.scrollTop = Math.min(
                    element.scrollTop + step,
                    element.scrollHeight
                );
                return {
                    before,
                    after: element.scrollTop,
                    atEnd: element.scrollTop + element.clientHeight
                        >= element.scrollHeight - 2
                };
            }
            """
        )
        await random_delay(450, 650)

        maximum_scroll_height = max(
            maximum_scroll_height, await list_locator.evaluate("e => e.scrollHeight")
        )
        current_state = await list_locator.evaluate(
            """
            element => ({
                top: element.scrollTop,
                height: element.scrollHeight,
                client: element.clientHeight
            })
            """
        )
        genuinely_at_end = (
            current_state["top"] + current_state["client"]
            >= maximum_scroll_height - 2
            and current_state["height"] >= maximum_scroll_height
        )
        if genuinely_at_end and current_state["top"] == previous_position:
            end_confirmations += 1
        elif genuinely_at_end:
            end_confirmations = 1
        else:
            end_confirmations = 0
        previous_position = current_state["top"]

        # 虚拟列表可能在第一次“到底”后继续扩展，连续确认 4 次再结束。
        if end_confirmations >= 4:
            break

    for friend in await collect_visible_streak_friends(page):
        found.setdefault(friend.name, friend)

    friends = list(found.values())
    logging.info("共扫描到 %d 个火花好友", len(friends))
    for friend in friends:
        logging.info("  [火花] %s — %s", friend.name, friend.streak)
    return friends


async def open_friend_from_search(page: Page, friend: Friend) -> None:
    """搜索结果使用独立的“联系人 + 发私信”结构，不是普通会话条目。"""
    search = page.locator(
        '.LeftPanelHeadersearch input, input[placeholder="搜索"]'
    ).first
    await search.wait_for(state="visible")
    await search.fill("")
    await search.fill(friend.name)
    await random_delay(800, 1300)

    rows = page.locator(".SearchPanelitembox")
    for index in range(await rows.count()):
        row = rows.nth(index)
        try:
            title = (
                await row.locator(".SearchPanelitemtitle").first.inner_text()
            ).strip()
            if not chat_names_match(title, friend.name) or not await row.is_visible():
                continue
            chat_button = row.locator(".SearchPanelitemchat_btn").first
            await chat_button.wait_for(state="visible")
            await chat_button.click(delay=random.randint(50, 120))
            return
        except Exception:
            continue

    raise RuntimeError("搜索结果中没有找到该好友的“发私信”按钮")


async def current_chat_title(page: Page) -> str:
    selectors = (
        ".RightPanelHeadertitle",
        '[class*="RightPanelHeader"] [class*="title"]',
        '[class*="rightPanelHeader"] [class*="title"]',
    )
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.is_visible():
                return (await locator.inner_text()).strip()
        except Exception:
            continue
    return ""


async def switch_to_friend(page: Page, friend: Friend) -> None:
    await open_friend_from_search(page, friend)

    input_box = page.locator(
        '[data-e2e="msg-input"] [contenteditable="true"]'
    ).first
    await input_box.wait_for(state="visible", timeout=10000)

    deadline = asyncio.get_running_loop().time() + 10
    observed_title = ""
    while asyncio.get_running_loop().time() < deadline:
        observed_title = await current_chat_title(page)
        if chat_names_match(observed_title, friend.name):
            logging.info("[%s] 已确认切换到目标聊天", friend.name)
            return
        await asyncio.sleep(0.25)

    raise RuntimeError(
        f"聊天标题校验失败，期望“{friend.name}”，"
        f"实际“{observed_title or '未识别'}”"
    )


async def open_emoji_panel(page: Page) -> None:
    input_area = page.locator('[data-e2e="msg-input"]').first
    await input_area.wait_for(state="visible")
    button = input_area.locator("svg.messageMsgInputiconAction").first
    await button.wait_for(state="visible")
    await button.click(delay=random.randint(40, 100))
    await random_delay(600, 1000)


async def click_matching_emoji(
    page: Page, keywords: list[str]
) -> Optional[dict[str, object]]:
    """点击真正绑定发送事件的 imgBox，并返回名称及资源标识。"""
    normalized_keywords = [keyword.strip() for keyword in keywords if keyword.strip()]
    descriptions = page.locator(
        ".emojiEmojiItememojiItemDesc, [class*='emojiItemDesc']"
    )
    for index in range(await descriptions.count()):
        description = descriptions.nth(index)
        try:
            label = (await description.inner_text()).strip()
            matched = any(keyword in label for keyword in normalized_keywords)
            if not matched:
                continue

            item = description.locator(
                "xpath=ancestor::*[contains(@class,'emojiEmojiItem')][1]"
            )
            click_target = item.locator(
                ".emojiEmojiItemimgBox, [data-apm-action='EmojiItem']"
            ).first
            image = item.locator("img").first
            await item.scroll_into_view_if_needed()
            await click_target.wait_for(state="visible", timeout=5000)
            await image.wait_for(state="visible", timeout=5000)
            source = await image.get_attribute("src")
            if not source:
                continue
            resource_key = await image.evaluate(
                "image => new URL(image.src).pathname"
            )
            existing_sources = await page.evaluate(
                """
                resourceKey => [
                    ...document.querySelectorAll(
                        '.MessageBoxContentisFromMe .MessageItemEmojiimage'
                    )
                ].filter(image => {
                    try {
                        return new URL(image.src).pathname === resourceKey;
                    } catch {
                        return false;
                    }
                }).map(image => image.src)
                """,
                resource_key,
            )
            await click_target.click(delay=random.randint(40, 100))
            return {
                "name": label,
                "resourceKey": resource_key,
                "existingSources": existing_sources,
            }
        except Exception:
            continue

    # 兼容将描述放在 title/alt 中的其他表情面板版本。
    return await page.evaluate(
        """
        keywords => {
            const normalize = text => (text || '').replace(/\\s+/g, '').trim();
            const normalizedKeywords = keywords
                .map(keyword => normalize(keyword))
                .filter(Boolean);
            const visible = element => {
                if (!element) return false;
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden'
                    && style.display !== 'none'
                    && rect.width > 0
                    && rect.height > 0;
            };
            const matches = text => {
                const value = normalize(text);
                return normalizedKeywords.some(keyword => value.includes(keyword));
            };

            const attributed = document.querySelectorAll(
                'img[title], img[alt], [title]'
            );
            for (const element of attributed) {
                const label = element.getAttribute('title')
                    || element.getAttribute('alt')
                    || '';
                if (visible(element) && matches(label)) {
                    const resourceKey = element.src
                        ? new URL(element.src).pathname
                        : '';
                    const existingSources = resourceKey
                        ? [...document.querySelectorAll(
                            '.MessageBoxContentisFromMe .MessageItemEmojiimage'
                        )].filter(existing => {
                            try {
                                return new URL(existing.src).pathname === resourceKey;
                            } catch {
                                return false;
                            }
                        }).map(existing => existing.src)
                        : [];
                    element.click();
                    return {
                        name: normalize(label),
                        resourceKey,
                        existingSources
                    };
                }
            }
            return null;
        }
        """,
        normalized_keywords,
    )


async def scroll_emoji_panels(page: Page, reset: bool = False) -> bool:
    return await page.evaluate(
        """
        reset => {
            const candidates = document.querySelectorAll(
                '[class*="emoji"], [class*="Emoji"], '
                + '[class*="sticker"], [class*="Sticker"]'
            );
            let moved = false;
            for (const element of candidates) {
                const rect = element.getBoundingClientRect();
                if (
                    rect.width > 120
                    && rect.height > 100
                    && element.scrollHeight > element.clientHeight + 10
                ) {
                    const before = element.scrollTop;
                    element.scrollTop = reset
                        ? 0
                        : Math.min(
                            element.scrollTop
                                + Math.max(100, element.clientHeight * 0.8),
                            element.scrollHeight
                        );
                    if (element.scrollTop !== before) moved = true;
                }
            }
            return moved;
        }
        """,
        reset,
    )


def message_config() -> dict:
    return CONFIG.get("message", {})


def emoji_keywords() -> list[str]:
    configured = message_config().get("emoji_keywords", ["早上好", "续火"])
    if isinstance(configured, str):
        configured = [configured]
    keywords = [str(keyword).strip() for keyword in configured if str(keyword).strip()]
    return keywords or ["早上好", "续火"]


def message_type() -> str:
    configured = str(message_config().get("type", "emoji")).strip().lower()
    return configured if configured in {"emoji", "text"} else "emoji"


def text_message() -> str:
    return str(message_config().get("text", "早上好")).strip() or "早上好"


async def find_and_click_emoji(page: Page) -> Optional[dict[str, object]]:
    for keyword in emoji_keywords():
        for _ in range(12):
            matched = await click_matching_emoji(page, [keyword])
            if matched:
                return matched
            if not await scroll_emoji_panels(page):
                break
            await random_delay(150, 300)
        await scroll_emoji_panels(page, reset=True)
        await random_delay(150, 250)
    return None


async def send_selected_emoji(page: Page, friend: Friend) -> str:
    title = await current_chat_title(page)
    if not chat_names_match(title, friend.name):
        raise RuntimeError(
            f"发送前标题校验失败，期望“{friend.name}”，"
            f"实际“{title or '未识别'}”"
        )

    await open_emoji_panel(page)
    selected = await find_and_click_emoji(page)
    if not selected:
        await page.keyboard.press("Escape")
        raise RuntimeError(
            "表情栏中没有找到配置的表情关键词："
            + "、".join(emoji_keywords())
        )

    # 抖音的大表情点击即发送，不会进入文本输入框。
    # 只有聊天区出现同资源的“我方大表情消息”才算成功。
    resource_key = selected.get("resourceKey", "")
    existing_sources = selected.get("existingSources", [])
    if not resource_key:
        raise RuntimeError("无法读取所选表情的资源标识")

    verified = False
    deadline = asyncio.get_running_loop().time() + 12
    while asyncio.get_running_loop().time() < deadline:
        verified = await page.evaluate(
            """
            ({resourceKey, existingSources}) => {
                const images = document.querySelectorAll(
                    '.MessageBoxContentisFromMe .MessageItemEmojiimage'
                );
                return [...images].some(image => {
                    try {
                        return new URL(image.src).pathname === resourceKey
                            && !existingSources.includes(image.src);
                    } catch {
                        return false;
                    }
                });
            }
            """,
            {
                "resourceKey": resource_key,
                "existingSources": existing_sources,
            },
        )
        if verified:
            break
        await asyncio.sleep(0.25)

    if not verified:
        raise RuntimeError(
            f"点击“{selected['name']}”后，聊天记录中未出现对应的我方表情"
        )

    logging.info("[%s] 已验证发送成功：%s", friend.name, selected["name"])
    return f"表情：{selected['name']}"


async def send_text_message(page: Page, friend: Friend) -> str:
    title = await current_chat_title(page)
    if not chat_names_match(title, friend.name):
        raise RuntimeError(
            f"发送前标题校验失败，期望“{friend.name}”，"
            f"实际“{title or '未识别'}”"
        )

    text = text_message()
    existing_count = await page.evaluate(
        """
        text => [...document.querySelectorAll('.MessageBoxContentisFromMe')]
            .filter(item => (item.innerText || '').includes(text)).length
        """,
        text,
    )
    input_box = page.locator(
        '[data-e2e="msg-input"] [contenteditable="true"]'
    ).first
    await input_box.wait_for(state="visible")
    await input_box.click()
    await input_box.fill(text)
    await random_delay(100, 250)
    await page.keyboard.press("Enter")

    verified = False
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        current_count = await page.evaluate(
            """
            text => [...document.querySelectorAll('.MessageBoxContentisFromMe')]
                .filter(item => (item.innerText || '').includes(text)).length
            """,
            text,
        )
        if current_count > existing_count:
            verified = True
            break
        await asyncio.sleep(0.25)

    if not verified:
        raise RuntimeError(f"发送“{text}”后，聊天记录中未出现新的我方文字消息")

    logging.info("[%s] 已验证发送成功：文字消息", friend.name)
    return f"文字：{text}"


async def send_configured_message(page: Page, friend: Friend) -> str:
    if message_type() == "text":
        return await send_text_message(page, friend)
    return await send_selected_emoji(page, friend)


async def process_friend(
    page: Page, friend: Friend, worker_id: int
) -> Result:
    retries = max(1, int(CONFIG.get("behavior", {}).get("max_retries", 3)))
    last_error = "未知错误"

    for attempt in range(1, retries + 1):
        try:
            logging.info(
                "[工作页%d] [%s] 第 %d/%d 次尝试",
                worker_id,
                friend.name,
                attempt,
                retries,
            )
            await switch_to_friend(page, friend)
            sent = await send_configured_message(page, friend)
            return Result(friend, True, f"已发送：{sent}", worker_id)
        except Exception as exc:
            last_error = str(exc)
            logging.warning(
                "[工作页%d] [%s] 尝试失败：%s",
                worker_id,
                friend.name,
                last_error,
            )
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            if attempt < retries:
                await random_delay(800, 1500)

    await save_failure_screenshot(page, friend.name, "send_failed")
    return Result(friend, False, last_error, worker_id)


async def worker(
    context: BrowserContext,
    queue: asyncio.Queue,
    results: list[Result],
    worker_id: int,
) -> None:
    page = await context.new_page()
    page.set_default_timeout(
        int(CONFIG.get("behavior", {}).get("page_timeout_ms", 30000))
    )
    try:
        await open_chat_page(page)
        while True:
            friend = await queue.get()
            if friend is None:
                queue.task_done()
                return
            try:
                results.append(
                    await process_friend(page, friend, worker_id)
                )
            except Exception as exc:
                logging.exception(
                    "[工作页%d] [%s] 未处理异常", worker_id, friend.name
                )
                results.append(Result(friend, False, str(exc), worker_id))
            finally:
                queue.task_done()
            await random_delay(500, 1200)
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def run_once() -> int:
    started_at = datetime.now()
    logging.info("=" * 60)
    logging.info(
        "抖音自动续火花 v6 - %s",
        started_at.strftime("%Y-%m-%d %H:%M:%S"),
    )
    logging.info("=" * 60)

    if already_success_today():
        logging.info("今天已经成功运行过，本次触发跳过，避免重复续火花")
        return 0

    info = get_browser_info()
    executable = find_executable(info)
    logging.info("浏览器：%s；EXE：%s", info.name, executable)

    if not executable:
        logging.error("没有找到浏览器可执行文件")
        return 1
    if not os.path.exists(info.user_data_dir):
        logging.error("浏览器用户数据目录不存在：%s", info.user_data_dir)
        return 1
    if browser_running(info.process_name) and not stop_browser(info.process_name):
        return 1

    context: Optional[BrowserContext] = None
    async with async_playwright() as playwright:
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=info.user_data_dir,
                executable_path=executable,
                headless=bool(
                    CONFIG.get("behavior", {}).get("headless", False)
                ),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                ignore_default_args=["--enable-automation"],
            )

            discovery_page = (
                context.pages[0] if context.pages else await context.new_page()
            )
            discovery_page.set_default_timeout(
                int(
                    CONFIG.get("behavior", {}).get(
                        "page_timeout_ms", 30000
                    )
                )
            )
            if not await ensure_logged_in(discovery_page):
                return 6
            friends = await scan_all_streak_friends(discovery_page)
            await discovery_page.close()

            excluded_names = set(
                CONFIG.get("targets", {}).get("excluded_names", [])
            )
            if excluded_names:
                friends = [
                    friend
                    for friend in friends
                    if friend.name not in excluded_names
                ]
                logging.info(
                    "排除名单生效：%s；剩余 %d 个目标",
                    "、".join(sorted(excluded_names)),
                    len(friends),
                )

            if not friends:
                logging.info("没有找到火花好友，脚本结束")
                mark_success_today("no_streak_friends")
                return 0

            parallel = CONFIG.get("parallel", {})
            requested = (
                int(parallel.get("max_concurrent", 3))
                if parallel.get("enabled", True)
                else 1
            )
            concurrency = max(1, min(3, requested, len(friends)))
            logging.info(
                "将使用 %d 个并发工作页面处理 %d 个好友",
                concurrency,
                len(friends),
            )

            queue: asyncio.Queue = asyncio.Queue()
            for friend in friends:
                queue.put_nowait(friend)
            for _ in range(concurrency):
                queue.put_nowait(None)

            results: list[Result] = []
            tasks = [
                asyncio.create_task(
                    worker(context, queue, results, worker_id)
                )
                for worker_id in range(1, concurrency + 1)
            ]
            await queue.join()
            await asyncio.gather(*tasks)

            successes = [result for result in results if result.success]
            failures = [result for result in results if not result.success]
            elapsed = (datetime.now() - started_at).total_seconds()
            logging.info("-" * 50)
            logging.info(
                "完成：成功 %d，失败 %d，耗时 %.0f 秒",
                len(successes),
                len(failures),
                elapsed,
            )
            for result in failures:
                logging.error(
                    "[失败] %s：%s", result.friend.name, result.detail
                )
            if failures:
                return 2
            mark_success_today(f"sent={len(successes)}")
            return 0
        finally:
            if context and CONFIG.get("behavior", {}).get(
                "close_browser_after", True
            ):
                await context.close()
                logging.info("浏览器已关闭")


async def async_main() -> int:
    timeout_seconds = int(
        CONFIG.get("behavior", {}).get("timeout_seconds", 600)
    )
    try:
        return await asyncio.wait_for(run_once(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logging.error("整体运行超过 %d 秒，已停止", timeout_seconds)
        return 3
    except PlaywrightTimeout as exc:
        logging.error("页面等待超时：%s", exc)
        return 4
    except Exception:
        logging.exception("脚本发生未处理异常")
        return 5


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
