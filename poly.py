import curses
import json
import time
from datetime import datetime
import requests

BASE = "https://gamma-api.polymarket.com"


def fetch_events(limit=200, tag_id=None, tag_slug=None):
    params = {
        "order": "id",
        "ascending": "false",
        "closed": "false",
        "limit": limit,
    }
    if tag_id:
        params["tag_id"] = tag_id
    if tag_slug:
        params["tag_slug"] = tag_slug
    r = requests.get(f"{BASE}/events", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_tags(limit=50):
    # Gamma /tags: liste de tous les tags. [web:42][web:104][web:130]
    r = requests.get(f"{BASE}/tags", timeout=10)
    r.raise_for_status()
    tags = r.json()

    # tags est typiquement une liste de dicts: {id, label, slug, market_count, ...}
    # On essaie de les trier par "market_count" desc si dispo, sinon par id.
    def sort_key(t):
        if isinstance(t, dict):
            if "market_count" in t:
                try:
                    return float(t["market_count"])
                except Exception:
                    return 0.0
            if "rank" in t:
                try:
                    return float(t["rank"])
                except Exception:
                    return 0.0
        return 0.0

    tags.sort(key=sort_key, reverse=True)
    return tags[:limit]


def _get_volume24(m):
    return (
        m.get("volume24hr")
        or m.get("volume24h")
        or m.get("volume24Hr")
        or m.get("volume_24hr")
        or 0
    )


def format_tags(ev):
    raw_tags = ev.get("tags") or ev.get("categories") or []
    tags = []
    for t in raw_tags:
        if isinstance(t, dict):
            label = t.get("label") or t.get("slug")
            if label:
                tags.append(str(label))
        else:
            tags.append(str(t))
    if not tags:
        return ""
    return ", ".join(tags)[:30]


def _format_end_date(m):
    raw = (
        m.get("endDate")
        or m.get("closeTime")
        or m.get("resolutionTime")
        or m.get("eventDate")
    )
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return str(raw)[:10]


def format_market_line(m, width):
    q = m.get("question", "")[: width - 40]

    yes_price = m.get("yesPrice")
    no_price = m.get("noPrice")

    if (yes_price is None or no_price is None) and m.get("outcomePrices"):
        try:
            prices = json.loads(m["outcomePrices"])
            if isinstance(prices, list) and len(prices) >= 2:
                yes_price = float(prices[0])
                no_price = float(prices[1])
        except Exception:
            pass

    yes_pct = f"{yes_price*100:.1f}%" if isinstance(yes_price, (int, float)) else "n/a"
    no_pct = f"{no_price*100:.1f}%" if isinstance(no_price, (int, float)) else "n/a"

    vol24 = _get_volume24(m)
    try:
        vol24_str = f"{float(vol24):.0f}"
    except Exception:
        vol24_str = str(vol24)

    end_str = _format_end_date(m)

    base = f"{q}  [Y:{yes_pct} N:{no_pct}]"
    extra = f" V24:{vol24_str} End:{end_str}"
    line = (base + " " + extra)[: width]
    return line


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(100)

    # Charge les tags (top 50 par market_count/rank si dispo) [web:42][web:104]
    tags = fetch_tags(limit=50)
    selected_tag_idx = 0
    current_tag = tags[0] if tags else None

    # Charge les events pour le tag courant
    if current_tag:
        events = fetch_events(
            tag_id=current_tag.get("id"),
            tag_slug=current_tag.get("slug"),
        )
    else:
        events = fetch_events()

    selected_event = 0

    REFRESH_INTERVAL = 10
    last_refresh = time.time()

    volume_filter_enabled = False
    volume_threshold = 1000.0  # volume minimal en 24h (USDC)

    while True:
        now = time.time()
        if now - last_refresh > REFRESH_INTERVAL:
            try:
                if current_tag:
                    events = fetch_events(
                        tag_id=current_tag.get("id"),
                        tag_slug=current_tag.get("slug"),
                    )
                else:
                    events = fetch_events()
                selected_event = min(selected_event, max(0, len(events) - 1))
                last_refresh = now
            except Exception:
                last_refresh = now

        stdscr.clear()
        h, w = stdscr.getmaxyx()

        col_tags = max(20, w // 6)
        col_events = max(25, w // 3)
        col_markets = w - col_tags - col_events - 1

        # Colonne tags (gauche) : liste des tags /tags
        stdscr.addstr(0, 0, "Tags (/tags)")
        for i, t in enumerate(tags[: h - 2]):
            y = 1 + i
            label = t.get("label") or t.get("slug") or "<unnamed>"
            mc = t.get("market_count") or t.get("markets_count") or ""
            prefix = "> " if i == selected_tag_idx else "  "
            extra = f" (m:{mc})" if mc != "" else ""
            line = f"{prefix}{label}{extra}"
            stdscr.addstr(y, 0, line[: col_tags - 1])

        # Colonne events (milieu)
        stdscr.addstr(0, col_tags, "Events")
        for i, ev in enumerate(events[: h - 2]):
            y = 1 + i
            prefix = "> " if i == selected_event else "  "
            title = ev.get("title", "")[: col_events - 3]
            stdscr.addstr(y, col_tags, prefix + title)

        # Colonne markets (droite)
        vol_flag = "Vfilter:on" if volume_filter_enabled else "Vfilter:off"
        stdscr.addstr(
            0,
            col_tags + col_events,
            f"Markets (probs, V24, End, {vol_flag})"[: col_markets - 1],
        )

        if events:
            ev = events[selected_event]
            markets = ev.get("markets", [])

            if volume_filter_enabled:
                markets = [
                    m
                    for m in markets
                    if float(_get_volume24(m) or 0) >= volume_threshold
                ]

            for j, m in enumerate(markets[: h - 2]):
                y = 1 + j
                line = format_market_line(m, col_markets - 2)
                stdscr.addstr(y, col_tags + col_events, line)

        # Barre du bas
        help_text = "[↑/↓] tags  [←/→] events  [v] vol-filter  [r] refresh  [q] quit"
        ts = datetime.fromtimestamp(last_refresh).strftime("%H:%M:%S")
        ts_text = f"Last refresh: {ts}"

        left_width = max(0, w - len(ts_text) - 1)
        stdscr.addstr(h - 1, 0, help_text[:left_width])
        stdscr.addstr(h - 1, max(0, w - len(ts_text) - 1), ts_text[: w - 1])

        stdscr.refresh()

        ch = stdscr.getch()
        if ch == ord("q"):
            break
        elif ch == curses.KEY_UP:
            # navigate tags up
            if tags:
                selected_tag_idx = max(0, selected_tag_idx - 1)
                current_tag = tags[selected_tag_idx]
                try:
                    events = fetch_events(
                        tag_id=current_tag.get("id"),
                        tag_slug=current_tag.get("slug"),
                    )
                    selected_event = 0
                    last_refresh = time.time()
                except Exception:
                    pass
        elif ch == curses.KEY_DOWN:
            # navigate tags down
            if tags:
                selected_tag_idx = min(len(tags) - 1, selected_tag_idx + 1)
                current_tag = tags[selected_tag_idx]
                try:
                    events = fetch_events(
                        tag_id=current_tag.get("id"),
                        tag_slug=current_tag.get("slug"),
                    )
                    selected_event = 0
                    last_refresh = time.time()
                except Exception:
                    pass
        elif ch == curses.KEY_LEFT:
            selected_event = max(0, selected_event - 1)
        elif ch == curses.KEY_RIGHT:
            selected_event = min(len(events) - 1, selected_event + 1)
        elif ch == ord("r"):
            try:
                # refresh events seulement (tags restent stables)
                if current_tag:
                    events = fetch_events(
                        tag_id=current_tag.get("id"),
                        tag_slug=current_tag.get("slug"),
                    )
                else:
                    events = fetch_events()
                selected_event = min(selected_event, max(0, len(events) - 1))
                last_refresh = time.time()
            except Exception:
                pass
        elif ch == ord("v"):
            volume_filter_enabled = not volume_filter_enabled


if __name__ == "__main__":
    curses.wrapper(main)

