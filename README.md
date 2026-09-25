# poly-client

```
          Poly-client
  Polymarket CLI Client - Terminal Interface
            v0.9
```

## Features

- **Polymarket API Client**: Fetches events and markets from Polymarket API
- **Event Navigation**: Arrow keys (↑/↓) to navigate events
- **Market Navigation**: Arrow keys (←/→) to navigate markets within selected event
- **Event Filtering**: Filters significant events (volume ≥ 1M, liquidity ≥ 10K)
- **Market Sorting**: Markets sorted by YES outcome price (descending)
- **Caching**: 60-second cache for API responses
- **Text Mode Fallback**: Works in terminals without curses support

## Usage

### Requirements
- Python 3.14+
- Terminal with curses support (or falls back to text mode)

### Installation
```bash
git clone https://github.com/yourusername/poly-client.git
cd poly-client
```

### Running
```bash
python3 poly.py
```

### Controls
- **↑/↓**: Navigate events (selected event stays at top)
- **←/→**: Navigate markets within selected event
- **r**: Refresh all events
- **v**: Toggle volume filter (1000 USDC minimum)
- **d**: Toggle event descriptions (show/hide)
- **q**: Quit application

### Text Mode
If terminal doesn't support curses, automatically falls back to text mode showing first 5 events with 3 markets each.

## Technical Details

- **API Endpoint**: `https://gamma-api.polymarket.com`
- **Static connection**: Connects directly to `104.18.34.205`, preserving the official hostname for TLS/SNI, certificate verification, and the HTTP `Host` header. No hostname DNS lookup is needed. Environment proxies are ignored and redirects are rejected to keep requests on the pinned endpoint.
- **IP maintenance**: This is a Cloudflare edge address and may change. Update `API_IP` in `poly.py` if it stops working; `172.64.153.51` was also verified during initial testing.
- **Cache TTL**: 60 seconds
- **Default Limits**: 1000 events, significant events only
- **Market Sorting**: By `outcomePrices[0]` (YES outcome) descending
- **Description Toggle**: Press 'd' to show/hide event descriptions
