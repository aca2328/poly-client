import curses
import unittest
from unittest.mock import patch

import poly


class Window:
    def __init__(self, height, width):
        self.height, self.width = height, width
        self.writes = []

    def getmaxyx(self):
        return self.height, self.width

    def addnstr(self, y, x, text, limit, attr):
        assert 0 <= y < self.height
        assert 0 <= x < self.width
        assert x + len(text[:limit]) < self.width
        self.writes.append((y, x, text[:limit]))

    def bkgd(self, *args): pass
    def erase(self): pass
    def box(self): pass
    def noutrefresh(self): pass
    def keypad(self, value): pass
    def timeout(self, value): pass


class DetailViewTests(unittest.TestCase):
    def setUp(self):
        self.event = poly._prepare_event({
            'title': 'Long event title ' * 50,
            'markets': [{
                'question': 'Long market question ' * 50,
                'outcomePrices': '["0.6", "0.4"]',
                'bestBid': 0.59, 'bestAsk': 0.61,
                'resolutionSource': 'https://example.com/' + 'x' * 200,
            }],
        })
        self.market = self.event['markets'][0]

    def test_labels_and_long_values_fit_without_overlap(self):
        pairs = [('Best Bid / Ask', '59% / 61%'), ('Resolution', 'x' * 200)]
        for width in (18, 30, 72):
            rows = poly.layout_detail_rows(pairs, width)
            for label, value, x in rows:
                self.assertLessEqual(len(label), width)
                self.assertLessEqual(x + len(value), width)
                if label and value:
                    self.assertLess(len(label), x)
            self.assertEqual(sum(value.count('x') for _, value, _ in rows), 200)

    def test_popup_bounds_and_last_field_reachable(self):
        for height, width in ((1, 1), (5, 15), (8, 24), (24, 80), (60, 160)):
            screen = Window(height, width)
            windows = []

            def newwin(h, w, y, x):
                self.assertLessEqual(y + h, height)
                self.assertLessEqual(x + w, width)
                window = Window(h, w)
                windows.append(window)
                return window

            with patch.object(curses, 'newwin', side_effect=newwin), \
                 patch.object(curses, 'color_pair', return_value=0), \
                 patch.object(curses, 'doupdate'):
                offset, page, maximum = poly.draw_market_detail_overlay(
                    screen, self.event, self.market, 0, 'Refresh failed', 100000
                )
            if height < 8 or width < 24:
                self.assertFalse(windows)
            else:
                self.assertEqual(offset, maximum)
                self.assertGreater(page, 0)
                self.assertTrue(any('Refresh failed' in text
                                    for _, _, text in windows[-1].writes))

    def test_keyboard_scroll_and_reopen_reset(self):
        screen = Window(24, 80)
        screen.getch = iter([
            10, curses.KEY_DOWN, curses.KEY_NPAGE, curses.KEY_UP,
            curses.KEY_PPAGE, curses.KEY_END, curses.KEY_HOME,
            27, 10, ord('q'),
        ]).__next__
        offsets = []

        def overlay(*args):
            offsets.append(args[-1])
            return args[-1], 10, 50

        with patch.object(poly, 'load_events', return_value=[self.event]), \
             patch.object(poly, 'init_colors'), \
             patch.object(curses, 'curs_set'), \
             patch.object(poly, 'draw_screen', return_value=0), \
             patch.object(poly, 'draw_market_detail_overlay', side_effect=overlay):
            poly.main(screen)
        self.assertEqual(offsets, [0, 1, 11, 10, 0, 50, 0, 0])


if __name__ == '__main__':
    unittest.main()
