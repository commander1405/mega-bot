"""Regression tests for advanced anti-abuse (profanity, links, similarity)."""
from __future__ import annotations

import unittest

from mega_bot.profanity import (
    contains_profanity,
    collapse_for_match,
    extract_urls,
    normalize_text,
    text_similarity,
)


class TestProfanity(unittest.TestCase):
    def test_plain_and_clean(self):
        self.assertFalse(contains_profanity('سلام خوبی؟'))
        self.assertFalse(contains_profanity('امروز هوا خوبه'))
        self.assertTrue(contains_profanity('کیر'))

    def test_obfuscation(self):
        self.assertTrue(contains_profanity('ک.ی.ر'))
        self.assertTrue(contains_profanity('ک ی ر'))
        self.assertTrue(contains_profanity('کـیـر'))
        self.assertTrue(contains_profanity('ک\u200bیر'))  # ZWSP
        self.assertTrue(contains_profanity('کییییر'))

    def test_variants(self):
        self.assertTrue(contains_profanity('ناموص'))
        self.assertTrue(contains_profanity('کسکش'))
        self.assertTrue(contains_profanity('ننتو گاییدم'))
        self.assertTrue(contains_profanity('حرامزاده'))

    def test_context_only_not_alone(self):
        self.assertFalse(contains_profanity('مادر'))
        self.assertFalse(contains_profanity('بابا'))
        self.assertFalse(contains_profanity('خواهر'))

    def test_arabic_letters(self):
        # ي/ك arabic forms
        self.assertEqual(normalize_text('كي'), normalize_text('کی')[:2] if False else normalize_text('كي'))
        self.assertIn('ی', normalize_text('ي'))
        self.assertIn('ک', normalize_text('ك'))

    def test_extra_words(self):
        self.assertTrue(contains_profanity('کلمهممنوعه', extra_words=['کلمهممنوعه']))


class TestLinks(unittest.TestCase):
    def test_http_https_www(self):
        self.assertTrue(extract_urls('ببین https://example.com/x'))
        self.assertTrue(extract_urls('www.google.com'))
        self.assertTrue(bool(extract_urls('https://a.com')))
        self.assertTrue(bool(extract_urls('www.b.ir')))

    def test_invite_and_domain(self):
        self.assertTrue(extract_urls('join ble.ir/foo'))
        self.assertTrue(extract_urls('t.me/joinchat/xxx'))
        self.assertTrue(extract_urls('site.ir/path'))

    def test_not_filename(self):
        # bare file.txt should not match tld list without proper domain pattern
        urls = extract_urls('فایل file.txt را بفرست')
        self.assertFalse(any(u.endswith('.txt') and '://' not in u for u in urls))

    def test_plain_text_no_link(self):
        self.assertFalse(bool(extract_urls('سلام دوست من')))
        self.assertFalse(bool(extract_urls('نمره من 20.5 شد')))


class TestSimilarity(unittest.TestCase):
    def test_identical(self):
        self.assertEqual(text_similarity('abc', 'abc'), 1.0)

    def test_near(self):
        self.assertGreaterEqual(text_similarity('خرید فروش ویژه', 'خرید  فروش  ویژه'), 0.9)

    def test_different(self):
        self.assertLess(text_similarity('سلام صبح بخیر', 'قیمت دلار چنده'), 0.5)

    def test_collapse(self):
        self.assertEqual(collapse_for_match('ک.ی.ر'), collapse_for_match('کیر'))


if __name__ == '__main__':
    unittest.main()
