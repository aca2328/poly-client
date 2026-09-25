import curses
import json
import os
import textwrap
import time
from datetime import datetime
from functools import lru_cache

import requests

API_HOST = "gamma-api.polymarket.com"
API_IP = "104.18.34.205"
BASE = f"https://{API_IP}"
VERSION = "0.9"
DEFAULT_EVENT_LIMIT = 1000
DEFAULT_VOLUME_MIN = 1_000_000
DEFAULT_LIQUIDITY_MIN = 10_000
REFRESH_INTERVAL = 10
REQUEST_TIMEOUT = 10

class StaticAPIAdapter(requests.adapters.HTTPAdapter):
    """Connect to the static IP while authenticating the official API hostname."""

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs.update(server_hostname=API_HOST, assert_hostname=API_HOST)
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


session = requests.Session()
# Environment proxies could resolve the hostname themselves or require DNS.
session.trust_env = False
session.headers["Host"] = API_HOST
session.mount(f"{BASE}/", StaticAPIAdapter())


def init_colors():
    """Initialize color pairs for better visibility."""
    curses.start_color()
    curses.use_default_colors()

    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_BLUE, -1)
    curses.init_pair(5, curses.COLOR_CYAN, -1)
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)
    curses.init_pair(7, curses.COLOR_WHITE, -1)


class APIError(Exception):
    pass


class Cache:
    def __init__(self, ttl=300):
        self.ttl = ttl
        self._cache = {}

    def get(self, key):
        item = self._cache.get(key)
        if item is not None and time.time() - item["timestamp"] < self.ttl:
            return item["data"]
        return None

    def set(self, key, data):
        self._cache[key] = {"data": data, "timestamp": time.time()}

    def clear(self, key=None):
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()


cache = Cache(ttl=60)


def fetch_events(
    limit=DEFAULT_EVENT_LIMIT,
    tag_id=None,
    tag_slug=None,
    volume_min=None,
    liquidity_min=None,
    force_refresh=False,
):
    cache_key = f"events_{tag_id or tag_slug or 'all'}_{limit}_{volume_min}_{liquidity_min}"
    cached = cache.get(cache_key)
    if not force_refresh and cached is not None:
        return cached

    try:
        params = {
            "order": "volume",
            "ascending": "false",
            "closed": "false",
            "active": "true",
            "limit": limit,
        }
        if tag_id:
            params["tag_id"] = tag_id
        if tag_slug:
            params["tag_slug"] = tag_slug
        if volume_min:
            params["volumeMin"] = volume_min
        if liquidity_min:
            params["liquidityMin"] = liquidity_min

        response = session.get(
            f"{BASE}/events", params=params, timeout=REQUEST_TIMEOUT, allow_redirects=False
        )
        if response.is_redirect:
            raise APIError("Events endpoint redirected; update the static API endpoint.")
        response.raise_for_status()
        data = response.json()
        cache.set(cache_key, data)
        return data
    except requests.exceptions.RequestException as e:
        raise APIError(f"Failed to fetch events: {e}") from e


def fetch_tags(limit=50, force_refresh=False):
    cache_key = f"tags_{limit}"
    cached = cache.get(cache_key)
    if not force_refresh and cached is not None:
        return cached

    try:
        response = session.get(
            f"{BASE}/tags", timeout=REQUEST_TIMEOUT, allow_redirects=False
        )
        if response.is_redirect:
            raise APIError("Tags endpoint redirected; update the static API endpoint.")
        response.raise_for_status()
        tags = response.json()

        def sort_key(tag):
            if isinstance(tag, dict):
                if "market_count" in tag:
                    return _coerce_float(tag["market_count"], 0.0)
                if "rank" in tag:
                    return _coerce_float(tag["rank"], 0.0)
            return 0.0

        tags.sort(key=sort_key, reverse=True)
        result = tags[:limit]
        cache.set(cache_key, result)
        return result
    except requests.exceptions.RequestException as e:
        raise APIError(f"Failed to fetch tags: {e}") from e


def _coerce_float(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_outcome_prices(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()

    if not isinstance(value, list):
        return ()

    return tuple(_coerce_float(price, None) for price in value)


def _first_present(mapping, *keys):
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return None


def _get_volume24(market):
    return (
        market.get("volume24hr")
        or market.get("volume24h")
        or market.get("volume24Hr")
        or market.get("volume_24hr")
        or 0
    )


def format_tags(event):
    raw_tags = event.get("tags") or event.get("categories") or []
    tags = []
    for tag in raw_tags:
        if isinstance(tag, dict):
            label = tag.get("label") or tag.get("slug")
            if label:
                tags.append(str(label))
        else:
            tags.append(str(tag))
    if not tags:
        return ""
    return ", ".join(tags)[:30]


def _format_end_date(market):
    raw = (
        market.get("endDate")
        or market.get("closeTime")
        or market.get("resolutionTime")
        or market.get("eventDate")
    )
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(raw)[:10]


def _format_compact_number(value):
    number = _coerce_float(value, None)
    if number is None:
        return "n/a"

    abs_number = abs(number)
    if abs_number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.1f}B"
    if abs_number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if abs_number >= 1_000:
        return f"{number / 1_000:.1f}K"
    if number.is_integer():
        return f"{number:.0f}"
    return f"{number:.2f}"


def _format_probability(value):
    number = _coerce_float(value, None)
    if number is None:
        return "n/a"
    if -1 <= number <= 1:
        return f"{number * 100:.1f}%"
    return f"{number:.3f}"


def _format_usd_price(value):
    number = _coerce_float(value, None)
    if number is None:
        return "n/a"
    if abs(number) < 1:
        return f"${number:.3f}"
    return f"${number:.2f}"


def _format_probability_change(value):
    number = _coerce_float(value, None)
    if number is None:
        return "n/a"
    if -1 <= number <= 1:
        return f"{number * 100:+.1f} pts"
    return f"{number:+.1f}"


def _derive_market_status(event, market):
    if market.get("closed") is True or event.get("closed") is True:
        return "Closed"
    if market.get("active") is False or event.get("active") is False:
        return "Inactive"
    if market.get("acceptingOrders") is False:
        return "No orders"
    return "Active"


def _pick_price_with_source(candidates):
    for label, value in candidates:
        number = _coerce_float(value, None)
        if number is not None:
            return number, label
    return None, None


def _prepare_market(market):
    if market.get("_prepared"):
        return market

    outcome_prices = _parse_outcome_prices(market.get("outcomePrices"))
    yes_price = _coerce_float(market.get("yesPrice"), None)
    no_price = _coerce_float(market.get("noPrice"), None)

    if yes_price is None and outcome_prices:
        yes_price = outcome_prices[0]
    if no_price is None and len(outcome_prices) > 1:
        no_price = outcome_prices[1]

    vol24 = _coerce_float(_get_volume24(market), 0.0)
    liquidity = _coerce_float(
        _first_present(market, "liquidityClob", "clobLiquidity", "liquidity", "liquidityNum"),
        None,
    )
    best_bid = _coerce_float(
        _first_present(market, "bestBid", "bestBidPrice", "bid", "bidPrice"),
        None,
    )
    best_ask = _coerce_float(
        _first_present(market, "bestAsk", "bestAskPrice", "ask", "askPrice"),
        None,
    )
    last_trade = _coerce_float(
        _first_present(
            market,
            "lastTradePrice",
            "lastPrice",
            "lastTradedPrice",
            "lastTradedPriceYes",
        ),
        None,
    )
    mid_price = _coerce_float(
        _first_present(
            market,
            "midPrice",
            "midpoint",
            "markPrice",
            "marketPrice",
            "currentPrice",
            "price",
            "priceNum",
        ),
        None,
    )
    day_move = _coerce_float(
        _first_present(
            market,
            "oneDayPriceChange",
            "priceChange24hr",
            "priceChange24h",
            "oneDayPriceChangePercent",
        ),
        None,
    )
    spread = None
    if best_bid is not None and best_ask is not None and best_ask >= best_bid:
        spread = best_ask - best_bid
    if mid_price is None and spread is not None:
        mid_price = (best_bid + best_ask) / 2

    buy_yes_price, buy_yes_source = _pick_price_with_source(
        (
            ("ask", best_ask),
            ("mark", mid_price),
            ("yes", yes_price),
            ("last", last_trade),
        )
    )
    sell_yes_price, sell_yes_source = _pick_price_with_source(
        (
            ("bid", best_bid),
            ("mark", mid_price),
            ("yes", yes_price),
            ("last", last_trade),
        )
    )

    market["_question"] = str(market.get("question", ""))
    market["_yes_price"] = yes_price
    market["_no_price"] = no_price
    market["_yes_pct"] = f"{yes_price * 100:.1f}%" if yes_price is not None else "n/a"
    market["_no_pct"] = f"{no_price * 100:.1f}%" if no_price is not None else "n/a"
    market["_yes_sort_key"] = yes_price if yes_price is not None else -1.0
    market["_vol24"] = vol24
    market["_vol24_str"] = f"{vol24:.0f}"
    market["_vol24_compact_str"] = _format_compact_number(vol24)
    market["_end_str"] = _format_end_date(market)
    market["_liquidity"] = liquidity
    market["_liquidity_str"] = _format_compact_number(liquidity)
    market["_best_bid"] = best_bid
    market["_best_bid_str"] = _format_probability(best_bid)
    market["_best_ask"] = best_ask
    market["_best_ask_str"] = _format_probability(best_ask)
    market["_mid_price"] = mid_price
    market["_mid_price_str"] = _format_probability(mid_price)
    market["_last_trade"] = last_trade
    market["_last_trade_str"] = _format_probability(last_trade)
    market["_day_move"] = day_move
    market["_day_move_str"] = _format_probability_change(day_move)
    market["_spread"] = spread
    market["_spread_str"] = (
        f"{spread * 100:.1f} pts" if spread is not None and -1 <= spread <= 1 else "n/a"
    )
    market["_buy_yes_price"] = buy_yes_price
    market["_buy_yes_price_str"] = _format_usd_price(buy_yes_price)
    market["_buy_yes_source"] = buy_yes_source
    market["_sell_yes_price"] = sell_yes_price
    market["_sell_yes_price_str"] = _format_usd_price(sell_yes_price)
    market["_sell_yes_source"] = sell_yes_source
    market["_resolution_source"] = str(
        _first_present(market, "resolutionSource", "rulesPrimary", "resolutionCriteria") or ""
    )
    market["_prepared"] = True
    return market


def _prepare_event(event):
    if event.get("_prepared"):
        return event

    event["_title"] = str(event.get("title", ""))
    event["_description"] = str(event.get("description", ""))

    markets = event.get("markets") or []
    for market in markets:
        _prepare_market(market)

    event["_markets_sorted"] = tuple(
        sorted(markets, key=lambda market: market["_yes_sort_key"], reverse=True)
    )
    event["_filtered_market_cache"] = {}
    event["_prepared"] = True
    return event


def prepare_events(events):
    return [_prepare_event(event) for event in (events or [])]


def get_visible_markets(event, volume_filter_enabled=False, volume_threshold=0.0):
    if not event:
        return ()

    markets = event.get("_markets_sorted", ())
    if not volume_filter_enabled:
        return markets

    filtered_cache = event.setdefault("_filtered_market_cache", {})
    cache_key = float(volume_threshold)
    if cache_key not in filtered_cache:
        filtered_cache[cache_key] = tuple(
            market for market in markets if market.get("_vol24", 0.0) >= volume_threshold
        )
    return filtered_cache[cache_key]


def load_events(force_refresh=False):
    events = fetch_events(
        limit=DEFAULT_EVENT_LIMIT,
        volume_min=DEFAULT_VOLUME_MIN,
        liquidity_min=DEFAULT_LIQUIDITY_MIN,
        force_refresh=force_refresh,
    )
    return prepare_events(events)


def clamp_index(index, size):
    if size <= 0:
        return 0
    return max(0, min(index, size - 1))


@lru_cache(maxsize=4096)
def wrap_text(text, max_width):
    """Wrap text to fit within max_width, handling line breaks and special characters."""
    if not text or max_width <= 0:
        return ()

    sanitized = (
        str(text)
        .replace("“", '"')
        .replace("”", '"')
        .replace("’", "'")
        .replace("–", "-")
        .replace("\n", " ")
        .replace("\r", " ")
    )
    words = sanitized.split()
    if not words:
        return ()

    lines = []
    current_line = words[0]

    for word in words[1:]:
        if len(current_line) + len(word) + 1 <= max_width:
            current_line += " " + word
        else:
            lines.append(current_line)
            current_line = word

    lines.append(current_line)
    return tuple(lines)


def format_market_line(market, width):
    if width <= 0:
        return ""

    question_width = max(0, width - 40)
    question = market.get("_question", "")[:question_width]
    base = f"{question}  [Y:{market.get('_yes_pct', 'n/a')} N:{market.get('_no_pct', 'n/a')}]"
    extra = f" V24:{market.get('_vol24_str', '0')} End:{market.get('_end_str', '')}"
    return (base + " " + extra)[:width]


def format_market_detail_pairs(event, market):
    pairs = [
        ("Event", event.get("_title", "")),
        ("Market", market.get("_question", "")),
        ("YES / NO", f"{market.get('_yes_pct', 'n/a')} / {market.get('_no_pct', 'n/a')}"),
    ]

    if market.get("_buy_yes_price") is not None:
        source = market.get("_buy_yes_source", "n/a")
        pairs.append(("Buy 1 YES", f"{market.get('_buy_yes_price_str', 'n/a')} ({source})"))

    if market.get("_sell_yes_price") is not None:
        source = market.get("_sell_yes_source", "n/a")
        pairs.append(("Sell 1 YES", f"{market.get('_sell_yes_price_str', 'n/a')} ({source})"))

    if market.get("_mid_price") is not None:
        pairs.append(("Mid / Mark", market.get("_mid_price_str", "n/a")))

    if market.get("_best_bid") is not None or market.get("_best_ask") is not None:
        pairs.append(
            (
                "Best Bid / Ask",
                f"{market.get('_best_bid_str', 'n/a')} / {market.get('_best_ask_str', 'n/a')}",
            )
        )

    if market.get("_spread") is not None:
        pairs.append(("Spread", market.get("_spread_str", "n/a")))

    if market.get("_last_trade") is not None:
        pairs.append(("Last Trade", market.get("_last_trade_str", "n/a")))

    pairs.append(("24h Vol", market.get("_vol24_compact_str", "n/a")))

    if market.get("_liquidity") is not None:
        pairs.append(("Liquidity", market.get("_liquidity_str", "n/a")))

    if market.get("_day_move") is not None:
        pairs.append(("24h Move", market.get("_day_move_str", "n/a")))

    if market.get("_end_str"):
        pairs.append(("End", market.get("_end_str", "")))

    pairs.append(("Status", _derive_market_status(event, market)))

    resolution_source = market.get("_resolution_source") or str(event.get("resolutionSource") or "")
    if resolution_source:
        pairs.append(("Resolution", resolution_source))

    return tuple(pairs)


def draw_text(stdscr, y, x, text, attr=0):
    if not text:
        return

    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    if x < 0:
        text = text[-x:]
        x = 0

    max_chars = w - x - 1
    if max_chars <= 0:
        return

    try:
        stdscr.addnstr(y, x, text, max_chars, attr)
    except curses.error:
        pass


def layout_detail_rows(pairs, width):
    """Wrap every field into scrollable rows, including long URLs and titles."""
    label_width = max((len(label) + 2 for label, _ in pairs), default=0)
    stacked = width - label_width < 20
    rows = []
    for label, value in pairs:
        if stacked:
            rows.extend((line, "", 0) for line in textwrap.wrap(f"{label}:", width))
            value_width = width
        else:
            value_width = width - label_width
        lines = textwrap.wrap(str(value), value_width) or [""]
        for index, line in enumerate(lines):
            rows.append((
                f"{label}:" if not stacked and index == 0 else "",
                line,
                0 if stacked else label_width,
            ))
    return rows



def draw_screen(
    stdscr,
    events,
    selected_event,
    selected_market,
    show_descriptions,
    volume_filter_enabled,
    volume_threshold,
    last_refresh,
    error_message,
):
    h, w = stdscr.getmaxyx()
    stdscr.erase()

    col_events = min(max(40, w // 2), max(1, w - 1))
    col_markets = max(1, w - col_events)

    desc_indicator = "📝 ON" if show_descriptions else "📝 OFF"
    draw_text(
        stdscr,
        0,
        0,
        f"📊 Events (Title {desc_indicator}) 📊",
        curses.color_pair(4) | curses.A_BOLD,
    )
    draw_text(stdscr, 1, 0, "─" * col_events, curses.color_pair(4))

    available_height = max(0, h - 3)
    scroll_offset = 0
    if events and available_height:
        max_possible_scroll = max(0, len(events) - available_height)
        scroll_offset = min(selected_event, max_possible_scroll)

        if len(events) > available_height:
            scroll_indicator = (
                f" 📜 [{scroll_offset + 1}-{min(scroll_offset + available_height, len(events))}/{len(events)}]"
            )
            draw_text(
                stdscr,
                1,
                max(0, col_events - len(scroll_indicator) - 1),
                scroll_indicator,
                curses.color_pair(4),
            )

        y = 2
        visible_events = events[scroll_offset : scroll_offset + available_height]
        for offset, event in enumerate(visible_events):
            if y >= h - 1:
                break

            actual_index = scroll_offset + offset
            is_selected = actual_index == selected_event

            prefix = "➤ " if is_selected else "  "
            marker = "🔘" if is_selected else "○"
            title_color = (
                curses.color_pair(5) | curses.A_BOLD
                if is_selected
                else curses.color_pair(7) | curses.A_BOLD
            )
            desc_color = (
                curses.color_pair(6)
                if is_selected
                else curses.color_pair(6) | curses.A_DIM
            )

            title_lines = wrap_text(event.get("_title", ""), col_events - 5)
            if not title_lines:
                title_lines = ("(untitled)",)

            for line_num, title_line in enumerate(title_lines):
                if y >= h - 1:
                    break
                if line_num == 0:
                    draw_text(stdscr, y, 0, f"{marker} {prefix}{title_line}", title_color)
                else:
                    draw_text(stdscr, y, 0, f"    {title_line}", title_color)
                y += 1

            if show_descriptions:
                desc_lines = wrap_text(f"✎ {event.get('_description', '')}", col_events - 3)
                for desc_line in desc_lines:
                    if y >= h - 1:
                        break
                    draw_text(stdscr, y, 2, desc_line, desc_color)
                    y += 1

            if y < h - 1:
                draw_text(
                    stdscr,
                    y,
                    0,
                    "┄" * col_events,
                    curses.color_pair(4) | curses.A_DIM,
                )
                y += 1
    elif not error_message:
        draw_text(
            stdscr,
            max(2, h // 2),
            max(0, (w // 2) - 8),
            "No events found.",
            curses.color_pair(3) | curses.A_BOLD,
        )

    vol_flag = "🔘 Vfilter:ON" if volume_filter_enabled else "○ Vfilter:OFF"
    market_header = f"💰 Markets (probs, V24, End, {vol_flag})"
    draw_text(
        stdscr,
        0,
        col_events,
        market_header[: max(0, col_markets - 1)],
        curses.color_pair(4) | curses.A_BOLD,
    )
    draw_text(stdscr, 1, col_events, "─" * col_markets, curses.color_pair(4))

    visible_markets = ()
    if events:
        visible_markets = get_visible_markets(
            events[selected_event],
            volume_filter_enabled=volume_filter_enabled,
            volume_threshold=volume_threshold,
        )

    selected_market = clamp_index(selected_market, len(visible_markets))
    for index, market in enumerate(visible_markets[: max(0, h - 2)]):
        y = 2 + index
        if y >= h - 1:
            break

        line = format_market_line(market, col_markets - 3)
        yes_price = market.get("_yes_price")
        if yes_price is None:
            color = curses.color_pair(7)
        elif yes_price > 0.6:
            color = curses.color_pair(2)
        elif yes_price < 0.4:
            color = curses.color_pair(1)
        else:
            color = curses.color_pair(3)

        prefix = "➤ " if index == selected_market else "  "
        if index == selected_market:
            color |= curses.A_BOLD | curses.A_UNDERLINE

        draw_text(stdscr, y, col_events, prefix + line, color)

    help_text = " 🔼/🔽 Events  ◀/▶ Markets  ⏎ Details  🔘 V-filter  📝 Desc  🔄 Refresh  🚪 Quit "
    ts_text = f" ⏱️  {datetime.fromtimestamp(last_refresh).strftime('%H:%M:%S')} "

    if h > 2:
        draw_text(stdscr, h - 2, 0, "─" * w, curses.color_pair(4))

    version_text = f" 🏷️  v{VERSION} "
    left_width = max(0, w - len(ts_text) - len(version_text) - 2)
    draw_text(stdscr, h - 1, 0, help_text[:left_width], curses.color_pair(4) | curses.A_BOLD)
    draw_text(
        stdscr,
        h - 1,
        max(0, w - len(ts_text) - len(version_text) - 1),
        version_text,
        curses.color_pair(6) | curses.A_BOLD,
    )
    draw_text(
        stdscr,
        h - 1,
        max(0, w - len(ts_text) - 1),
        ts_text,
        curses.color_pair(2) | curses.A_BOLD,
    )

    if error_message:
        error_y = h - 3 if h > 3 else 0
        error_text = f" ⚠️  ERROR: {error_message[: max(0, w - 12)]} ⚠️ "
        draw_text(
            stdscr,
            error_y,
            0,
            error_text,
            curses.color_pair(1) | curses.A_BOLD | curses.A_BLINK,
        )

    stdscr.noutrefresh()
    curses.doupdate()
    return selected_market


def draw_market_detail_overlay(
    stdscr, event, market, last_refresh, error_message, scroll_offset=0
):
    h, w = stdscr.getmaxyx()
    if h < 8 or w < 24:
        draw_text(stdscr, 0, 0, "Resize for details")
        stdscr.noutrefresh()
        curses.doupdate()
        return scroll_offset, 1, 0

    popup_height = h - 2
    popup_width = w - 2
    popup = curses.newwin(popup_height, popup_width, 1, 1)
    popup.bkgd(" ", curses.color_pair(7))
    popup.erase()
    popup.box()

    draw_text(popup, 0, 2, " Details ", curses.color_pair(4) | curses.A_BOLD)
    ts_text = datetime.fromtimestamp(last_refresh).strftime("%H:%M:%S")
    if popup_width >= 32:
        draw_text(popup, 0, popup_width - 10, ts_text, curses.color_pair(2))

    pairs = list(format_market_detail_pairs(event, market))
    if error_message:
        pairs.append(("Refresh error", error_message))
    rows = layout_detail_rows(pairs, popup_width - 4)
    page_size = popup_height - 4
    max_scroll = max(0, len(rows) - page_size)
    scroll_offset = min(max(0, scroll_offset), max_scroll)
    for y, (label, value, value_x) in enumerate(
        rows[scroll_offset:scroll_offset + page_size], start=1
    ):
        draw_text(popup, y, 2, label, curses.color_pair(5) | curses.A_BOLD)
        draw_text(popup, y, 2 + value_x, value, curses.color_pair(7))

    position = f"{scroll_offset + 1}-{min(scroll_offset + page_size, len(rows))}/{len(rows)}"
    draw_text(popup, popup_height - 3, 2, position, curses.color_pair(3))
    footer = "Up/Down PgUp/PgDn Home/End | Esc Back | q Quit"
    if popup_width < len(footer) + 4:
        footer = "Up/Dn Esc Back q Quit"
    draw_text(popup, popup_height - 2, 2, footer[:popup_width - 4], curses.color_pair(4))
    popup.noutrefresh()
    curses.doupdate()
    return scroll_offset, page_size, max_scroll



def main(stdscr):
    try:
        curses.curs_set(0)
    except curses.error:
        pass

    stdscr.keypad(True)
    init_colors()

    events = []
    error_message = None
    show_descriptions = False
    volume_filter_enabled = False
    volume_threshold = 1000.0
    selected_event = 0
    selected_market = 0
    detail_view_open = False
    detail_scroll = 0
    detail_page_size = 1
    detail_max_scroll = 0

    last_refresh = time.time()
    next_refresh = last_refresh + REFRESH_INTERVAL
    needs_redraw = True
    previous_size = stdscr.getmaxyx()

    try:
        events = load_events()
        error_message = None
    except APIError as e:
        error_message = str(e)
    finally:
        last_refresh = time.time()
        next_refresh = last_refresh + REFRESH_INTERVAL

    while True:
        h, w = stdscr.getmaxyx()
        if (h, w) != previous_size:
            previous_size = (h, w)
            stdscr.clear()
            needs_redraw = True

        selected_event = clamp_index(selected_event, len(events))
        if events:
            visible_markets = get_visible_markets(
                events[selected_event],
                volume_filter_enabled=volume_filter_enabled,
                volume_threshold=volume_threshold,
            )
            selected_market = clamp_index(selected_market, len(visible_markets))
        else:
            visible_markets = ()
            selected_market = 0

        detail_view_open = detail_view_open and bool(events and visible_markets)

        if needs_redraw:
            selected_market = draw_screen(
                stdscr,
                events,
                selected_event,
                selected_market,
                show_descriptions,
                volume_filter_enabled,
                volume_threshold,
                last_refresh,
                error_message,
            )
            if detail_view_open and events and visible_markets:
                detail_scroll, detail_page_size, detail_max_scroll = draw_market_detail_overlay(
                    stdscr,
                    events[selected_event],
                    visible_markets[selected_market],
                    last_refresh,
                    error_message,
                    detail_scroll,
                )
            needs_redraw = False

        timeout_ms = max(0, int((next_refresh - time.time()) * 1000))
        stdscr.timeout(timeout_ms)
        ch = stdscr.getch()
        now = time.time()

        if ch == curses.ERR:
            if now >= next_refresh:
                try:
                    events = load_events(force_refresh=True)
                    error_message = None
                except APIError as e:
                    error_message = str(e)
                last_refresh = now
                next_refresh = now + REFRESH_INTERVAL
                needs_redraw = True
            continue

        if ch == curses.KEY_RESIZE:
            needs_redraw = True
            continue

        if ch == ord("q"):
            cache.clear()
            break

        if detail_view_open:
            if ch == 27:
                detail_view_open = False
                needs_redraw = True
            elif ch in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE,
                        curses.KEY_NPAGE, curses.KEY_HOME, curses.KEY_END):
                offsets = {
                    curses.KEY_UP: detail_scroll - 1,
                    curses.KEY_DOWN: detail_scroll + 1,
                    curses.KEY_PPAGE: detail_scroll - detail_page_size,
                    curses.KEY_NPAGE: detail_scroll + detail_page_size,
                    curses.KEY_HOME: 0,
                    curses.KEY_END: detail_max_scroll,
                }
                detail_scroll = min(max(0, offsets[ch]), detail_max_scroll)
                needs_redraw = True
            continue

        if ch in (curses.KEY_ENTER, 10, 13) and events and visible_markets:
            detail_view_open = True
            detail_scroll = 0
            needs_redraw = True
            continue

        if ch == curses.KEY_UP:
            new_selected_event = clamp_index(selected_event - 1, len(events))
            if new_selected_event != selected_event:
                selected_event = new_selected_event
                selected_market = 0
                needs_redraw = True
            continue

        if ch == curses.KEY_DOWN:
            new_selected_event = clamp_index(selected_event + 1, len(events))
            if new_selected_event != selected_event:
                selected_event = new_selected_event
                selected_market = 0
                needs_redraw = True
            continue

        if ch == curses.KEY_LEFT and events:
            visible_markets = get_visible_markets(
                events[selected_event],
                volume_filter_enabled=volume_filter_enabled,
                volume_threshold=volume_threshold,
            )
            new_selected_market = clamp_index(selected_market - 1, len(visible_markets))
            if new_selected_market != selected_market:
                selected_market = new_selected_market
                needs_redraw = True
            continue

        if ch == curses.KEY_RIGHT and events:
            visible_markets = get_visible_markets(
                events[selected_event],
                volume_filter_enabled=volume_filter_enabled,
                volume_threshold=volume_threshold,
            )
            new_selected_market = clamp_index(selected_market + 1, len(visible_markets))
            if new_selected_market != selected_market:
                selected_market = new_selected_market
                needs_redraw = True
            continue

        if ch == ord("r"):
            try:
                events = load_events(force_refresh=True)
                error_message = None
            except APIError as e:
                error_message = str(e)
            last_refresh = time.time()
            next_refresh = last_refresh + REFRESH_INTERVAL
            needs_redraw = True
            continue

        if ch == ord("v"):
            volume_filter_enabled = not volume_filter_enabled
            selected_market = 0
            needs_redraw = True
            continue

        if ch == ord("d"):
            show_descriptions = not show_descriptions
            needs_redraw = True


def text_mode_main():
    """Simple text-based interface for environments without curses support."""
    print("📊 Polymarket Client (Text Mode)")
    print(f"🏷️  Version {VERSION}")
    print("=" * 50)

    try:
        events = load_events()
        if not events:
            print("No events found.")
            return

        for index, event in enumerate(events[:5]):
            print(f"\n🔘 Event {index + 1}:")
            print(f"Title: {event.get('_title', 'N/A')}")
            print(f"Description: {event.get('_description', 'N/A')[:100]}...")

            markets = get_visible_markets(event)
            if markets:
                print(f"Markets ({len(markets)}):")
                for market_index, market in enumerate(markets[:3]):
                    print(f"  {market_index + 1}. {market.get('_question', 'N/A')}")
                    print(
                        f"     Yes: {market.get('_yes_pct', 'n/a')} | No: {market.get('_no_pct', 'n/a')}"
                    )
                    print(f"     Volume 24h: {market.get('_vol24_str', '0')}")

            if index < 4:
                print("-" * 50)
    except APIError as e:
        print(f"Error fetching data: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    if os.environ.get("TERM") == "dumb" or not os.isatty(0):
        print("Terminal does not support curses, using text mode...")
        text_mode_main()
    else:
        curses.wrapper(main)
