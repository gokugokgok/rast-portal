from auth_test_support import authorized_client
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import app
import searching


def page_result(number, size=30):
    return {'results': [{'url': f'https://example.com/{i}', 'title': f'Result {i}'}
                        for i in range((number - 1) * size, number * size)]}


class SearchSettingsTests(unittest.TestCase):
    def setUp(self):
        self.client = authorized_client(app.app)
        searching.search_cache.clear()
        app._used = 0
        self.factory_patch = patch('searching.requests.Session')
        self.factory = self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)
        self.session = self.factory.return_value.__enter__.return_value
        self.session.post.side_effect = self.upstream

    @staticmethod
    def upstream(url, **kwargs):
        response = MagicMock(status_code=200)
        response.json.return_value = page_result(kwargs['data']['pageno'])
        return response

    def search(self, **changes):
        return self.client.post('/', data={'q': 'Tokyo', **changes}, headers={'X-Portal-Request': '1'})

    def test_all_display_sizes_and_reuse(self):
        for count in searching.COUNTS:
            response = self.search(per_page=str(count))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_data(as_text=True).count('<article class="result-card'), count)
        # 120 pooled results fit all sizes; fetch each source page once.
        pages = [call.kwargs['data']['pageno'] for call in self.session.post.call_args_list]
        self.assertEqual(sorted(pages), [1, 2, 3, 4])

    def test_small_pages_have_no_gaps_or_repeated_results(self):
        first = self.search(per_page='10').get_data(as_text=True)
        second = self.search(per_page='10', page='1').get_data(as_text=True)
        self.assertIn('>Result 9</a>', first)
        self.assertNotIn('>Result 10</a>', first)
        self.assertIn('>Result 10</a>', second)
        self.assertNotIn('>Result 9</a>', second)
        self.assertIn('>Result 19</a>', second)
        self.assertEqual(self.session.post.call_count, 1)

    def test_region_mode_phrase_and_hidden_form_settings(self):
        response = self.search(region='en-US', mode='wide', match='phrase', per_page='40', category='videos', safesearch='2')
        self.assertEqual(response.status_code, 200)
        sent = self.session.post.call_args.kwargs['data']
        self.assertEqual(sent['language'], 'en-US')
        self.assertEqual(sent['q'], '"Tokyo"')
        self.assertEqual(sent['engines'], 'google videos,bing videos,duckduckgo videos,brave.videos')
        self.assertEqual(sent['timeout_limit'], 3.5)
        self.assertNotIn('categories', sent)
        markup = response.get_data(as_text=True)
        for key, value in [('region', 'en-US'), ('mode', 'wide'), ('match', 'phrase'), ('per_page', '40'), ('safesearch', '2')]:
            self.assertGreater(markup.count(f'name="{key}" value="{value}"'), 4)
        self.assertIn('data-region="en-US"', markup)

    def test_fast_mode_reduces_engines_and_wait(self):
        self.search(mode='fast', category='images')
        data = self.session.post.call_args.kwargs['data']
        self.assertEqual(data['engines'], 'google images,bing images')
        self.assertEqual(data['timeout_limit'], 1.5)
        self.assertNotIn('categories', data)

    def test_disabled_engines_are_never_explicitly_requested(self):
        settings = {'engines': [{'name': 'google', 'disabled': True}, {'name': 'bing', 'inactive': True}],
                    'use_default_settings': {'engines': {'keep_only': ['google', 'bing', 'duckduckgo']}}}
        enabled = searching.configured_engines(settings)
        self.assertEqual(enabled['all'], ('duckduckgo',))
        with patch.dict('searching.ACTIVE_ENGINES', enabled):
            self.search(mode='fast')
        self.assertEqual(self.session.post.call_args.kwargs['data']['engines'], 'duckduckgo')

    def test_cache_isolated_by_region_mode_and_match(self):
        for fields in ({}, {'region': 'en-US'}, {'mode': 'fast'}, {'match': 'phrase'}, {'region': 'all'}):
            self.search(**fields)
        self.assertEqual(self.session.post.call_count, 5)
        self.search(per_page='10')
        self.assertEqual(self.session.post.call_count, 5)

    def test_invalid_settings_never_reach_backend(self):
        for key, value in [('per_page', '0'), ('per_page', '10000'), ('per_page', 'x'), ('region', 'en-JP'), ('region', '<script>'), ('mode', 'unknown'), ('match', 'unknown')]:
            self.assertEqual(self.search(**{key: value}).status_code, 400)
        self.assertEqual(self.session.post.call_count, 0)

    def test_empty_unavailable_results_are_retried(self):
        self.session.post.side_effect = lambda *a, **k: MagicMock(status_code=200, json=lambda: {'results': [], 'unresponsive_engines': [['google', 'timeout']]})
        for _ in range(2):
            response = self.search()
            self.assertIn('検索元が応答しませんでした', response.get_data(as_text=True))
        self.assertEqual(self.session.post.call_count, 2)

    def test_large_jump_is_bounded_and_recovers(self):
        self.session.post.side_effect = lambda *a, **k: MagicMock(status_code=200, json=lambda: page_result(k['data']['pageno'], 1))
        response = self.search(per_page='100', page='9')
        self.assertEqual(self.session.post.call_count, 6)
        self.assertIn('取得済みのページを表示', response.get_data(as_text=True))
        self.assertIn('data-page="0"', response.get_data(as_text=True))
        self.assertEqual(response.get_data(as_text=True).count('<article class="result-card'), 6)

    def test_tracking_duplicates_and_distinct_images(self):
        self.assertEqual(searching.result_key({'url': 'https://EXAMPLE.com:443/a?x=1&utm_source=test#top'}, 'all'),
                         searching.result_key({'url': 'https://example.com/a?x=1'}, 'all'))
        self.assertNotEqual(searching.result_key({'url': 'https://example.com/a?x=1'}, 'all'),
                            searching.result_key({'url': 'https://example.com/a?x=2'}, 'all'))
        self.assertNotEqual(searching.result_key({'url': 'https://example.com/a', 'img_src': 'https://example.com/1.png'}, 'images'),
                            searching.result_key({'url': 'https://example.com/a', 'img_src': 'https://example.com/2.png'}, 'images'))
        self.assertIsNone(searching.result_key({'url': 'javascript:alert(1)'}, 'all'))
        self.assertIsNone(searching.result_key({'url': 'https://example.com:bad/'}, 'all'))

    def test_parallel_completion_keeps_source_order(self):
        started = threading.Barrier(3)
        third_done, second_done = threading.Event(), threading.Event()
        def fetch(query, category, safe, region, mode, match, number, budget):
            if number in (2, 3, 4):
                started.wait(timeout=2)
                if number == 2:
                    second_done.wait(timeout=2)
                elif number == 3:
                    third_done.wait(timeout=2)
                    second_done.set()
                else:
                    third_done.set()
            return {'results': page_result(number, 20)['results'], 'failed': False}
        with patch('searching.fetch_page', side_effect=fetch):
            result = searching.search_results('test', 'all', '1', 'ja-JP', 'balanced', 'normal', 0, 100, lambda: True)
        self.assertEqual([r['title'] for r in result['results']], [f'Result {i}' for i in range(100)])

    def test_failed_middle_page_is_retried_without_skipping(self):
        failing = True
        def fetch(query, category, safe, region, mode, match, number, budget):
            if number == 2 and failing:
                raise searching.SearchError()
            return {'results': page_result(number, 20)['results'], 'failed': False}
        with patch('searching.fetch_page', side_effect=fetch):
            first = searching.search_results('test', 'all', '1', 'ja-JP', 'balanced', 'normal', 0, 60, lambda: True)
            self.assertEqual(len(first['results']), 20)
            failing = False
            second = searching.search_results('test', 'all', '1', 'ja-JP', 'balanced', 'normal', 0, 60, lambda: True)
        self.assertEqual([r['title'] for r in second['results']], [f'Result {i}' for i in range(60)])

    def test_simultaneous_searches_share_fetch(self):
        def run():
            return searching.search_results('shared', 'all', '1', 'ja-JP', 'balanced', 'normal', 0, 20, lambda: True)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: run(), range(4)))
        self.assertTrue(all(len(result['results']) == 20 for result in results))
        self.assertEqual(self.session.post.call_count, 1)


if __name__ == '__main__':
    unittest.main()
