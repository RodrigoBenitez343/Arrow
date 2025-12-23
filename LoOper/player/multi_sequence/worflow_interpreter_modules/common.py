import logging
import os
import time
from ...config import logger

# Setup chain run logger
chain_logger = logging.getLogger('chain_run')
chain_logger.setLevel(logging.INFO)
chain_logger.propagate = False 
if not chain_logger.handlers:
    # Save run.log in the root directory (parent of LoOper folder usually, or CWD)
    # Using CWD as per user env
    log_path = os.path.join(os.getcwd(), 'run.log')
    try:
        # Force encoding to utf-8 and ensure file creation
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"Log initialized at {time.ctime()}\n")
            
        file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        chain_logger.addHandler(file_handler)
    except Exception as e:
        logger.error(f"Failed to setup chain_run logger: {e}")
