from .utils import get_logger

logger = get_logger(__name__)

class BaseNodeOperations:
    def __init__(self, parent_widget):
        logger.info("Initializing NodeOperations")
        try:
            self.parent_widget = parent_widget
            logger.debug(f"NodeOperations initialized with parent_widget: {type(parent_widget).__name__}")
        except Exception as e:
            logger.error(f"Error initializing NodeOperations: {e}")
            raise