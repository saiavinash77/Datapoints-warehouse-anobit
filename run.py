#!/usr/bin/env python3
"""One command to start the AVC collector: python run.py"""

import logging

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('collector.log', encoding='utf-8'), logging.StreamHandler()],
)

if __name__ == '__main__':
    print('AVC Collector running at http://localhost:8000  (Ctrl+C to stop)')
    uvicorn.run('server.app:app', host='0.0.0.0', port=8000, log_level='warning')
