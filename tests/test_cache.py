import unittest
from unittest.mock import patch
from cache import MemoryCache


class CacheTests(unittest.TestCase):
    def test_expiry_eviction_and_no_plaintext_keys(self):
        cache = MemoryCache(ttl=30, max_entries=2, max_bytes=100)
        with patch('cache.time.monotonic', return_value=0):
            cache.put({'value': 1}, 'secret-query')
            cache.put({'value': 2}, 'second')
            self.assertEqual(cache.get('secret-query'), {'value': 1})
            self.assertTrue(all(isinstance(key, bytes) and len(key) == 32 for key in cache._items))
            cache.put({'value': 3}, 'third')
            self.assertIsNone(cache.get('second'))
            cache.put({'value': 'x' * 200}, 'oversize')
            self.assertIsNone(cache.get('oversize'))
            self.assertLessEqual(cache._bytes, 100)
        with patch('cache.time.monotonic', return_value=31):
            self.assertIsNone(cache.get('secret-query'))
            self.assertEqual(cache._bytes, 0)
