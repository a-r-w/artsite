"""Structured (JSON) log formatter — one JSON object per line, no dependency.

Selected by LOG_FORMAT=json (the default in production); see settings.LOGGING.
Keeps logs parseable by Fly's log shipping / a log aggregator without pulling in
a logging library.
"""

import json
import logging


class JSONFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            'level': record.levelname,
            'time': self.formatTime(record, '%Y-%m-%dT%H:%M:%S%z'),
            'logger': record.name,
            'message': record.getMessage(),
        }
        if record.exc_info:
            payload['exc_info'] = self.formatException(record.exc_info)
        if record.stack_info:
            payload['stack_info'] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str)
