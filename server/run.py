"""Windowless Task Scheduler entrypoint; absolute paths and rotating server logs."""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
logs = root / 'server' / 'data' / 'logs'
logs.mkdir(parents=True, exist_ok=True)
handler = RotatingFileHandler(logs / 'server.log', maxBytes=2 * 1024 * 1024, backupCount=2, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler])

if __name__ == '__main__':
    uvicorn.run('server.main:app', host='127.0.0.1', port=8765,
                ws_max_size=8192, access_log=False, log_config=None)
