
"""Tests for group statistics dashboard image + command matching."""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from mega_bot.stats_dashboard import render_group_dashboard, fa_num


class TestStatsRender(unittest.TestCase):
    def test_render_with_data(self):
        days = [(date.today() - timedelta(days=6 - i)).isoformat() for i in range(7)]
        stats = {
            'total_msgs': 128,
            'active_users': 12,
            'warn_total': 3,
            'total_msgs_7': 400,
            'total_msgs_30': 1200,
            'tracking_start': days[0],
            'counts': {
                'joined': 2, 'left_count': 1,
                'text_count': 90, 'photo_count': 20, 'video_count': 5,
                'sticker_count': 8, 'voice_count': 3, 'document_count': 1, 'animation_count': 1,
            },
            'daily_7': [{'day': d, 'count': 10 + i * 7} for i, d in enumerate(days)],
            'top_users': [
                {'user_id': 1, 'name': 'علی', 'msg_count': 40},
                {'user_id': 2, 'name': 'سارا', 'msg_count': 22},
            ],
        }
        png = render_group_dashboard(title='گروه دوستان', stats=stats, members_count=50, chat_id=-1001)
        self.assertIsInstance(png, (bytes, bytearray))
        self.assertGreater(len(png), 5000)
        self.assertTrue(png[:8] == b'\x89PNG\r\n\x1a\n')

    def test_render_empty_stats(self):
        png = render_group_dashboard(
            title='خالی',
            stats={'total_msgs': 0, 'active_users': 0, 'counts': {}, 'daily_7': [], 'top_users': []},
            members_count=None,
            chat_id=-1002,
        )
        self.assertTrue(png.startswith(b'\x89PNG'))

    def test_fa_num(self):
        self.assertIn('۱', fa_num(1))
        self.assertIn('۰', fa_num(0))


class TestStatsCommands(unittest.TestCase):
    CMDS = {
        'آمار', 'امار', 'آمار گروه', 'آمارگروه', 'امار گروه',
        'فعالیت', 'فعالیت گروه', 'عملکرد', 'عملکرد گروه',
    }

    def test_exact_match(self):
        for c in self.CMDS:
            self.assertIn(c, self.CMDS)

    def test_no_incidental(self):
        # phrases that must NOT trigger
        bad = ['سلام آمار خوبه', 'فعالیت بدنی', 'عملکردت عالیه', 'آمارا', '']
        for b in bad:
            self.assertNotIn(b, self.CMDS)


if __name__ == '__main__':
    unittest.main()
