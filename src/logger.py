import logging
import os
import sys
from datetime import datetime

def setup_logger(log_dir):
    """FIX B4: Use getLogger with explicit handlers instead of basicConfig (which is a no-op after first call)."""
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, f"execution_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    
    # Get root logger and clear existing handlers
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Remove all existing handlers to prevent duplicate logging
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Add fresh handlers for this session
    logger.addHandler(logging.FileHandler(log_file))
    logger.addHandler(logging.StreamHandler(sys.stdout))
    
    # Set format for all handlers
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    for handler in logger.handlers:
        handler.setFormatter(formatter)

def log_step(message):
    logging.info(f"STEP: {message}")

def log_error(message):
    logging.error(message)
