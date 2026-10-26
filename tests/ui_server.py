"""Explicit test server for browser checks; all data lives in a temporary directory."""
import tempfile
from pathlib import Path

import uvicorn

from server.config import Settings
from server.main import create_app
from tests.test_app import FakeAI

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='screenshot-ui-test-') as directory:
        settings = Settings(token='ui-test-only', api_key='test-only', model='test-only', data_dir=Path(directory))
        uvicorn.run(create_app(settings, FakeAI()), host='127.0.0.1', port=8765, access_log=False)
