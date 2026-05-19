"""
scripts/run_scheduler.py
獨立啟動排程背景程序
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from backend.engine.scheduler import start

if __name__ == "__main__":
    logger.info("排程器啟動...")
    start()
