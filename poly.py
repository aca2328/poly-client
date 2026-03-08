import curses
import json
import time
from datetime import datetime
import requests
from functools import lru_cache

BASE = "https://gamma-api.polymarket.com"
VERSION = "0.9"

# Initialize curses colors
def init_colors():
    """Initialize color pairs for better visibility."""
    curses.start_color()
    curses.use_default_colors()
    
    # Define color pairs
    curses.init_pair(1, curses.COLOR_RED, -1)      # Error messages
    curses.init_pair(2, curses.COLOR_GREEN, -1)    # Success/positive
    curses.init_pair(3, curses.COLOR_YELLOW, -1)   # Highlights/warnings
    curses.init_pair(4, curses.COLOR_BLUE, -1)     # Headers
    curses.init_pair(5, curses.COLOR_CYAN, -1)     # Selected items
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)  # Descriptions
    curses.init_pair(7, curses.COLOR_WHITE, -1)    # Bright text

class APIError(Exception):
    pass

class Cache:
    def __init__(self, ttl=300):
        self.ttl = ttl
        self._cache = {}
    
    def get(self, key):
        item = self._cache.get(key)
        if item and time.time() - item['timestamp'] < self.ttl:
            return item['data']
        return None
    
    def set(self, key, data):
        self._cache[key] = {'data': data, 'timestamp': time.time()}
    
    def clear(self, key=None):
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

cache = Cache(ttl=60)


def fetch_events(limit=1000, tag_id=None, tag_slug=None, volume_min=None, liquidity_min=None):
    cache_key = f"events_{tag_id or tag_slug or 'all'}_{limit}_{volume_min}_{liquidity_min}"
    cached = cache.get(cache_key)
    if cached:
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
        r = requests.get(f"{BASE}/events", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        cache.set(cache_key, data)
        return data
    except requests.exceptions.RequestException as e:
        raise APIError(f"Failed to fetch events: {str(e)}")


def fetch_tags(limit=50):
    # Gamma /tags: liste de tous les tags. [web:42][web:104][web:130]
    cache_key = f"tags_{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
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
        result = tags[:limit]
        cache.set(cache_key, result)
        return result
    except requests.exceptions.RequestException as e:
        raise APIError(f"Failed to fetch tags: {str(e)}")


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

def wrap_text(text, max_width):
    """Wrap text to fit within max_width, handling line breaks and special characters."""
    if not text:
        return []
    
    # Sanitize text for curses display - replace problematic characters
    sanitized = text.replace('“', '"').replace('”', '"').replace('’', "'").replace('–', '-')
    # Replace newlines with spaces for wrapping
    sanitized = sanitized.replace('\n', ' ').replace('\r', ' ')
    
    words = sanitized.split(' ')
    lines = []
    current_line = ""
    
    for word in words:
        # Skip empty words
        if not word:
            continue
            
        # Check if adding this word would exceed the max width
        if len(current_line) + len(word) + 1 <= max_width:
            if current_line:
                current_line += " " + word
            else:
                current_line = word
        else:
            # Add current line to lines and start a new line
            if current_line:
                lines.append(current_line)
            current_line = word
    
    # Add the last line if it exists
    if current_line:
        lines.append(current_line)
    
    return lines


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
    # Optimize curses for reduced flickering
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(100)
    
    # Enable keypad for better input handling
    stdscr.keypad(True)
    
    # Initialize colors
    init_colors()

    # Charge tous les events (filtrés pour les plus significatifs)
    events = []
    error_message = None
    show_descriptions = False  # Start with descriptions hidden
    
    try:
        events = fetch_events(
            limit=1000,  # Increased from 100 to 1000
            volume_min=1000000,  # 1M minimum volume
            liquidity_min=10000  # 10k minimum liquidity
        )
    except APIError as e:
        error_message = str(e)

    selected_event = 0
    selected_market = 0  # Track selected market for navigation
    scroll_offset = 0    # Track scrolling position for events
    prev_h, prev_w = None, None  # Track previous screen dimensions

    REFRESH_INTERVAL = 10
    last_refresh = time.time()

    volume_filter_enabled = False
    volume_threshold = 1000.0  # volume minimal en 24h (USDC)

    while True:
        now = time.time()
        if now - last_refresh > REFRESH_INTERVAL:
            try:
                events = fetch_events(
                    limit=1000,  # Increased from 100 to 1000
                    volume_min=1000000,
                    liquidity_min=10000
                )
                selected_event = min(selected_event, max(0, len(events) - 1))
                last_refresh = now
                error_message = None
            except APIError as e:
                error_message = str(e)
                last_refresh = now

        # Optimized screen update to reduce flickering
        h, w = stdscr.getmaxyx()
        
        # Only do full clear if screen dimensions changed
        if prev_h != h or prev_w != w:
            stdscr.clear()
            prev_h, prev_w = h, w
        else:
            # For normal updates, use more efficient clearing
            stdscr.erase()
        
        # Use curses.doupdate() for more efficient screen updates
        # We'll call refresh() at the end instead of after each change

        col_events = max(40, w // 2)
        col_markets = w - col_events - 1

        # Loading indicator with color and animation
        if not events and not error_message:
            loading_texts = ["Loading data...", "Loading data.. ", "Loading data.  ", "Loading data   "]
            loading_text = loading_texts[int(time.time() * 2) % 4]  # Simple animation
            stdscr.addstr(h//2, w//2 - len(loading_text)//2, loading_text, curses.color_pair(3) | curses.A_BOLD)
            stdscr.addstr(h//2 + 1, w//2 - 8, "🔄 Please wait...", curses.color_pair(3))
            curses.doupdate()
            time.sleep(0.1)
            continue

        # Colonne events (gauche) - avec couleurs et ASCII art et scrolling
        desc_indicator = "📝 ON" if show_descriptions else "📝 OFF"
        stdscr.addstr(0, 0, f"📊 Events (Title {desc_indicator}) 📊", curses.color_pair(4) | curses.A_BOLD)
        
        # Draw a separator line
        separator = "─" * min(col_events, w - 1)
        stdscr.addstr(1, 0, separator, curses.color_pair(4))
        
        # Calculate visible events based on screen height
        available_height = h - 3  # Subtract header and separator lines
        
        # Fixed cursor position logic: selected event stays at top of screen
        # The selected event will always appear at the top (y=2)
        fixed_cursor_y = 2  # Top of the events area
        
        # Calculate scroll offset to keep selected event at top
        # We want selected_event to appear at the first position (index 0)
        cursor_list_position = 0  # Selected event at top of visible list
        
        # Calculate the maximum scroll position that keeps selected event visible
        max_scroll_for_selection = selected_event
        
        # But also ensure we don't scroll past the end of the list
        max_possible_scroll = max(0, len(events) - available_height)
        
        # Use the more restrictive of the two
        scroll_offset = min(max_scroll_for_selection, max_possible_scroll)
        
        # Ensure scroll_offset is not negative
        scroll_offset = max(0, scroll_offset)
        
        # Final check: if selected event would be scrolled out of view, adjust
        if selected_event >= scroll_offset + available_height:
            scroll_offset = selected_event - available_height + 1
            scroll_offset = max(0, min(scroll_offset, max_possible_scroll))
        
        # Show scroll position indicator if there are more events than fit on screen
        if len(events) > available_height:
            scroll_indicator = f" 📜 [{scroll_offset + 1}-{min(scroll_offset + available_height, len(events))}/{len(events)}]"
            stdscr.addstr(1, col_events - len(scroll_indicator) - 1, scroll_indicator, curses.color_pair(4))
        
        # Display events with selected event always at top
        y = 2
        
        # Display visible events starting from scroll_offset
        for i, ev in enumerate(events[scroll_offset:scroll_offset + available_height]):
            if y >= h - 1:  # Stop if we run out of space
                break
                
            # Calculate the actual event index
            actual_index = scroll_offset + i
            
            # Use different prefix and color for selected event
            if actual_index == selected_event:
                prefix = "➤ "
                title_color = curses.color_pair(5) | curses.A_BOLD
                desc_color = curses.color_pair(6)
                marker = "🔘"
            else:
                prefix = "  "
                title_color = curses.color_pair(7) | curses.A_BOLD
                desc_color = curses.color_pair(6) | curses.A_DIM
                marker = "○"
            
            title = ev.get("title", "")
            description = ev.get("description", "")
            
            # Wrap title to fit in available width
            title_lines = wrap_text(title, col_events - 5)  # Account for marker and prefix
            
            # Display title (first line with prefix and marker, subsequent lines indented)
            for line_num, title_line in enumerate(title_lines):
                if y >= h - 1:
                    break
                if line_num == 0:
                    # First line: marker + prefix + title
                    display_text = f"{marker} {prefix}{title_line}"
                    stdscr.addstr(y, 0, display_text, title_color)
                else:
                    # Subsequent lines: indented
                    indent = "    "  # Match marker + prefix width
                    stdscr.addstr(y, 0, indent + title_line, title_color)
                y += 1
            
            # Display description only if show_descriptions is True
            if show_descriptions and description and y < h - 1:
                desc_lines = wrap_text("✎ " + description, col_events - 3)
                for desc_line in desc_lines:
                    if y >= h - 1:
                        break
                    stdscr.addstr(y, 2, desc_line, desc_color)  # indented
                    y += 1
            
            # Add spacing between events with a subtle separator
            if y < h - 1:
                stdscr.addstr(y, 0, "┄" * min(col_events, w - 1), curses.color_pair(4) | curses.A_DIM)
                y += 1

        # Colonne markets (droite) - avec couleurs
        vol_flag = "🔘 Vfilter:ON" if volume_filter_enabled else "○ Vfilter:OFF"
        market_header = f"💰 Markets (probs, V24, End, {vol_flag})"
        stdscr.addstr(0, col_events, market_header[: col_markets - 1], curses.color_pair(4) | curses.A_BOLD)
        
        # Draw separator for markets column
        market_separator = "─" * min(col_markets, w - col_events - 1)
        stdscr.addstr(1, col_events, market_separator, curses.color_pair(4))

        if events:
            ev = events[selected_event]
            markets = ev.get("markets", [])

            if volume_filter_enabled:
                markets = [
                    m
                    for m in markets
                    if float(_get_volume24(m) or 0) >= volume_threshold
                ]

            # Sort markets by YES outcome price in descending order
            def get_yes_price(market):
                try:
                    outcome_prices = market.get("outcomePrices", "[]")
                    if isinstance(outcome_prices, str):
                        import json
                        prices = json.loads(outcome_prices)
                        if prices and len(prices) > 0:
                            return float(prices[0])  # First price is for "Yes" outcome
                    elif isinstance(outcome_prices, list) and outcome_prices:
                        return float(outcome_prices[0])  # First price is for "Yes" outcome
                    return 0.0
                except (ValueError, IndexError, json.JSONDecodeError):
                    return 0.0
            
            markets = sorted(
                markets,
                key=get_yes_price,
                reverse=True
            )

            for j, m in enumerate(markets[: h - 2]):
                y = 2 + j  # Start after header and separator
                if y >= h - 1:
                    break
                
                line = format_market_line(m, col_markets - 4)  # Less width for selection marker
                
                # Determine if this is the selected market
                if j == selected_market:
                    marker = "➤ "
                    line = marker + line
                else:
                    marker = "  "
                    line = marker + line
                
                # Color code based on yes_price if available
                yes_price = m.get("yesPrice")
                if yes_price and isinstance(yes_price, (int, float)):
                    if yes_price > 0.6:
                        color = curses.color_pair(2)  # Green for high probability
                    elif yes_price < 0.4:
                        color = curses.color_pair(1)  # Red for low probability
                    else:
                        color = curses.color_pair(3)  # Yellow for medium probability
                else:
                    color = curses.color_pair(7)  # White for unknown
                
                # Highlight selected market
                if j == selected_market:
                    color |= curses.A_BOLD | curses.A_UNDERLINE
                
                stdscr.addstr(y, col_events, line, color)

        # Barre du bas - avec couleurs et ASCII art
        help_text = " 🔼/🔽 Events  ◀/▶ Markets  🔘 V-filter  📝 Desc  🔄 Refresh  🚪 Quit "
        ts = datetime.fromtimestamp(last_refresh).strftime("%H:%M:%S")
        ts_text = f" ⏱️  {ts} "

        # Draw a separator above the footer
        if h > 2:
            footer_separator = "─" * w
            stdscr.addstr(h - 2, 0, footer_separator, curses.color_pair(4))

        # Display help text and timestamp with colors
        version_text = f" 🏷️  v{VERSION} "
        left_width = max(0, w - len(ts_text) - len(version_text) - 2)
        stdscr.addstr(h - 1, 0, help_text[:left_width], curses.color_pair(4) | curses.A_BOLD)
        stdscr.addstr(h - 1, max(0, w - len(ts_text) - len(version_text) - 1), version_text, curses.color_pair(6) | curses.A_BOLD)
        stdscr.addstr(h - 1, max(0, w - len(ts_text) - 1), ts_text[: w - 1], curses.color_pair(2) | curses.A_BOLD)
        
        # Error message display with better formatting
        if error_message:
            error_y = h - 3 if h > 3 else 0
            error_text = f" ⚠️  ERROR: {error_message[:w-12]} ⚠️ "
            stdscr.addstr(error_y, 0, error_text, curses.color_pair(1) | curses.A_BOLD | curses.A_BLINK)

        # Use curses.doupdate() for more efficient screen updates
        curses.doupdate()

        ch = stdscr.getch()
        if ch == ord("q"):
            # Clear cache when exiting
            cache.clear()
            break

        elif ch == curses.KEY_UP:
            # Navigate events up
            selected_event = max(0, selected_event - 1)
            # Ensure selected event stays visible
            if selected_event < scroll_offset:
                scroll_offset = selected_event
        elif ch == curses.KEY_DOWN:
            # Navigate events down
            selected_event = min(len(events) - 1, selected_event + 1)
            # Ensure selected event stays visible (scroll if needed)
            available_height = h - 3
            if selected_event >= scroll_offset + available_height:
                scroll_offset = selected_event - available_height + 1
                # Ensure we don't scroll past the end
                if len(events) > 0:
                    max_scroll = len(events) - 1  # Last possible scroll position
                    scroll_offset = min(scroll_offset, max_scroll)
        elif ch == curses.KEY_LEFT:
            # Navigate markets left (previous market)
            if events and len(events) > 0:
                markets = events[selected_event].get("markets", [])
                if volume_filter_enabled:
                    markets = [
                        m for m in markets
                        if float(_get_volume24(m) or 0) >= volume_threshold
                    ]
                # Sort markets by YES outcome price in descending order (same as display)
                def get_yes_price(market):
                    try:
                        outcome_prices = market.get("outcomePrices", "[]")
                        if isinstance(outcome_prices, str):
                            prices = json.loads(outcome_prices)
                            if prices and len(prices) > 0:
                                return float(prices[0])  # First price is for "Yes" outcome
                        elif isinstance(outcome_prices, list) and outcome_prices:
                            return float(outcome_prices[0])  # First price is for "Yes" outcome
                        return 0.0
                    except (ValueError, IndexError, json.JSONDecodeError):
                        return 0.0
                
                markets = sorted(
                    markets,
                    key=get_yes_price,
                    reverse=True
                )
                if markets:
                    selected_market = max(0, selected_market - 1)
        elif ch == curses.KEY_RIGHT:
            # Navigate markets right (next market)
            if events and len(events) > 0:
                markets = events[selected_event].get("markets", [])
                if volume_filter_enabled:
                    markets = [
                        m for m in markets
                        if float(_get_volume24(m) or 0) >= volume_threshold
                    ]
                # Sort markets by YES outcome price in descending order (same as display)
                def get_yes_price(market):
                    try:
                        outcome_prices = market.get("outcomePrices", "[]")
                        if isinstance(outcome_prices, str):
                            prices = json.loads(outcome_prices)
                            if prices and len(prices) > 0:
                                return float(prices[0])  # First price is for "Yes" outcome
                        elif isinstance(outcome_prices, list) and outcome_prices:
                            return float(outcome_prices[0])  # First price is for "Yes" outcome
                        return 0.0
                    except (ValueError, IndexError, json.JSONDecodeError):
                        return 0.0
                
                markets = sorted(
                    markets,
                    key=get_yes_price,
                    reverse=True
                )
                if markets:
                    selected_market = min(len(markets) - 1, selected_market + 1)
        elif ch == ord("r"):
            try:
                # refresh all events with filters
                events = fetch_events(
                    limit=100,
                    volume_min=1000000,
                    liquidity_min=10000
                )
                selected_event = min(selected_event, max(0, len(events) - 1))
                last_refresh = time.time()
                error_message = None
            except APIError as e:
                error_message = str(e)
                last_refresh = time.time()
        elif ch == ord("v"):
            volume_filter_enabled = not volume_filter_enabled
        elif ch == ord("d"):
            # Toggle description display
            show_descriptions = not show_descriptions


def text_mode_main():
    """Simple text-based interface for environments without curses support."""
    print("📊 Polymarket Client (Text Mode)")
    print(f"🏷️  Version {VERSION}")
    print("=" * 50)
    
    try:
        events = fetch_events(
            limit=1000,  # Increased from 10 to 1000
            volume_min=1000000,
            liquidity_min=10000
        )
        
        if not events:
            print("No events found.")
            return
            
        for i, event in enumerate(events[:5]):  # Show first 5 events
            print(f"\n🔘 Event {i+1}:")
            print(f"Title: {event.get('title', 'N/A')}")
            print(f"Description: {event.get('description', 'N/A')[:100]}...")
            
            markets = event.get('markets', [])
            if markets:
                print(f"Markets ({len(markets)}):")
                for j, market in enumerate(markets[:3]):  # Show first 3 markets
                    print(f"  {j+1}. {market.get('question', 'N/A')}")
                    print(f"     Yes: {market.get('yesPrice', 'N/A')} | No: {market.get('noPrice', 'N/A')}")
                    print(f"     Volume: {market.get('volume24', {}).get('amount', 'N/A')}")
            
            if i < 4:  # Don't print separator after last event
                print("-" * 50)
                
    except APIError as e:
        print(f"Error fetching data: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")

if __name__ == "__main__":
    import os
    # Check if terminal supports curses
    if os.environ.get('TERM') == 'dumb' or not os.isatty(0):
        print("Terminal does not support curses, using text mode...")
        # Fallback to text mode
        text_mode_main()
    else:
        curses.wrapper(main)

