import os
import tempfile
import time
import unittest
from unittest.mock import patch

from bot import data as D


class CandleFreshnessTests(unittest.TestCase):
    def make_recent(self, now_ms, count=10):
        step = D.GRANULARITY_MS["4H"]
        latest = now_ms - (now_ms % step) - step
        c = D.synthetic(n=count, step_ms=step)
        c.ts = [latest - (count - 1 - i) * step for i in range(count)]
        return c

    def test_latest_closed_bar_is_recent(self):
        now_ms = 1_800_000_000_000
        c = self.make_recent(now_ms)
        self.assertTrue(D.candles_are_recent(c, "4H", now_ms))

    def test_old_market_data_is_not_recent(self):
        now_ms = 1_800_000_000_000
        c = self.make_recent(now_ms)
        c.ts[-1] -= 30 * D.GRANULARITY_MS["4H"]
        self.assertFalse(D.candles_are_recent(c, "4H", now_ms))

    def test_fresh_file_timestamp_does_not_make_stale_cache_valid(self):
        now_ms = int(time.time() * 1000)
        step = D.GRANULARITY_MS["4H"]
        stale = self.make_recent(now_ms)
        stale.ts = [t - 30 * step for t in stale.ts]
        fresh = self.make_recent(now_ms)

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "BTCUSDT_4H.csv")
            D.save_csv(stale, path)
            os.utime(path, (time.time(), time.time()))
            with patch("bot.data.fetch_bitget", return_value=fresh) as fetch:
                loaded = D.load_or_fetch("BTCUSDT", "4H", 1, cache_dir=directory)
            fetch.assert_called_once_with("BTCUSDT", "4H", 1)
            self.assertEqual(loaded.ts[-1], fresh.ts[-1])

    def test_refuses_to_save_stale_data_returned_by_exchange(self):
        now_ms = int(time.time() * 1000)
        step = D.GRANULARITY_MS["4H"]
        stale = self.make_recent(now_ms)
        stale.ts = [t - 30 * step for t in stale.ts]

        with tempfile.TemporaryDirectory() as directory:
            with patch("bot.data.fetch_bitget", return_value=stale):
                with self.assertRaisesRegex(RuntimeError, "stale candles"):
                    D.load_or_fetch("BTCUSDT", "4H", 1, cache_dir=directory)
            self.assertFalse(os.path.exists(os.path.join(directory, "BTCUSDT_4H.csv")))


if __name__ == "__main__":
    unittest.main()
