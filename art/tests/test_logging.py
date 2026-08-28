"""Tests for the JSON log formatter (artsite/jsonlog.py)."""

import json
import logging
import sys

from django.test import SimpleTestCase

from artsite.jsonlog import JSONFormatter


class JSONFormatterTests(SimpleTestCase):
    def test_emits_one_json_object_with_core_fields(self):
        rec = logging.LogRecord('art.test', logging.INFO, __file__, 1, 'hello %s', ('world',), None)
        data = json.loads(JSONFormatter().format(rec))  # one valid JSON object
        self.assertEqual(data['level'], 'INFO')
        self.assertEqual(data['logger'], 'art.test')
        self.assertEqual(data['message'], 'hello world')  # %-args interpolated
        self.assertIn('time', data)

    def test_includes_exception_traceback(self):
        try:
            raise ValueError('boom')
        except ValueError:
            rec = logging.LogRecord('art.test', logging.ERROR, __file__, 1, 'failed', None, sys.exc_info())
        data = json.loads(JSONFormatter().format(rec))
        self.assertIn('ValueError: boom', data['exc_info'])
