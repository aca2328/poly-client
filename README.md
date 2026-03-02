# poly-client

```
          Poly-client
  Polymarket CLI Client - Terminal Interface
            v0.8
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
- **q**: Quit application

### Text Mode
If terminal doesn't support curses, automatically falls back to text mode showing first 5 events with 3 markets each.

## Technical Details

- **API Endpoint**: `https://gamma-api.polymarket.com`
- **Cache TTL**: 60 seconds
- **Default Limits**: 100 events, significant events only
- **Market Sorting**: By `outcomePrices[0]` (YES outcome) descending
