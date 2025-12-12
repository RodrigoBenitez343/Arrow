import logging

def get_logger(name):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.FileHandler('graphui_debug.log')
        console_handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.addHandler(console_handler)
        logger.setLevel(logging.DEBUG)
    return logger
