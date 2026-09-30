import logging

def get_logger(name):
    # ponytail: handler lives on LoOper.NGUI parent logger (main_window.py)
    return logging.getLogger(name)
