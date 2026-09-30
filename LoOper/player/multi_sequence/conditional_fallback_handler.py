import logging
logger = logging.getLogger(__name__)
# conditional_fallback_handler.py
"""
Conditional fallback handler for multi-sequence automation.
Entry point that orchestrates modular components for conditional fallback handling.
"""
from ..computer_vision import TemplateMatching
from ..sequence_player import SequencePlayer
from .conditional_fallback_resources import (
    ValidationCache,
    PathResolver,
    PresenceTrigger,
    AbsenceTrigger,
    OCRTrigger,
    ConditionalLoop,
    NodeExecutor
)


class ConditionalFallbackHandler:
    """Entry point for conditional fallback handling using modular components"""
    
    def __init__(self, stop_flag=None, chain_file_dir=None):
        self.stop_flag = stop_flag
        self.template_matcher = TemplateMatching()
        self.chain_file_dir = chain_file_dir
        
        # Initialize modular components
        self.validation_cache = ValidationCache()
        self.path_resolver = PathResolver(chain_file_dir)
        self.presence_trigger = PresenceTrigger(self.template_matcher, self.validation_cache, self.path_resolver, stop_flag)
        self.absence_trigger = AbsenceTrigger(self.template_matcher, self.validation_cache, self.path_resolver, stop_flag)
        self.ocr_trigger = OCRTrigger(self.template_matcher, self.validation_cache, self.path_resolver, stop_flag)
        self.conditional_loop = ConditionalLoop(
            self.template_matcher, self.validation_cache, self.path_resolver,
            self.presence_trigger, self.absence_trigger, self.ocr_trigger, stop_flag
        )
        self.node_executor = NodeExecutor(
            self.template_matcher, self.validation_cache, self.path_resolver,
            self.presence_trigger, self.absence_trigger, self.ocr_trigger,
            self.conditional_loop, stop_flag
        )
    
    # Delegation methods to modular components
    def _get_screen_hash(self, screen_cv=None):
        """Delegate to validation cache"""
        return self.validation_cache.get_screen_hash(screen_cv, self.template_matcher)
    
    def _resolve_path(self, path, add_json=False):
        """Delegate to path resolver"""
        return self.path_resolver.resolve_path(path, add_json)
    
    def check_presence_trigger(self, trigger_config, screen_cv=None):
        """Check for presence trigger with timeout support from trigger_config"""
        # Check if timeout is specified in trigger_config
        timeout = trigger_config.get('timeout')
        if timeout is not None:
            # Use timeout method if timeout is specified
            return self.presence_trigger.check_presence_with_timeout(trigger_config, timeout)
        else:
            # Use regular presence check if no timeout specified
            return self.presence_trigger.check_presence_trigger(trigger_config, screen_cv)
    
    def check_absence_trigger(self, trigger_config, timeout, screen_cv=None):
        """Delegate to absence trigger"""
        return self.absence_trigger.check_absence_trigger(trigger_config, timeout, screen_cv)
    
    def check_ocr_trigger(self, trigger_config, stop_flag=None):
        """Delegate to OCR trigger"""
        return self.ocr_trigger.check_ocr_trigger(trigger_config, stop_flag=stop_flag)
    
    def execute_conditional_node(self, node, stop_flag=None):
        """Delegate to node executor"""
        return self.node_executor.execute_conditional_node(node, stop_flag)
    
    def evaluate_conditional_from_node(self, conditional_node, stop_flag=None):
        """Delegate to node executor"""
        return self.node_executor.evaluate_conditional_from_node(conditional_node, stop_flag)
    
    def execute_conditional_loop_from_node(self, conditional_data, stop_flag=None):
        """Delegate to conditional loop"""
        return self.conditional_loop.execute_conditional_loop_from_node(conditional_data, stop_flag)
    
    def get_conditional_branch_node(self, node, result):
        """Delegate to node executor"""
        return self.node_executor.get_conditional_branch_node(node, result)
    
    def handle_until_present_condition(self, conditional_node, stop_flag=None):
        """Delegate to node executor for backward compatibility"""
        return self.node_executor.handle_until_present_condition(conditional_node, stop_flag)
    
    def handle_until_absent_condition(self, conditional_node, stop_flag=None):
        """Delegate to node executor for backward compatibility"""
        return self.node_executor.handle_until_absent_condition(conditional_node, stop_flag)
    
    def execute_conditional_loop(self, loop_config, sequence_player):
        """Delegate to conditional loop"""
        return self.conditional_loop.execute_conditional_loop(loop_config, sequence_player)
    
    def handle_pre_sequence_conditions(self, item, stop_flag, advanced_conditions):
        """
        Handle pre-sequence conditions using modular components.
        
        Args:
            item (dict): Sequence item configuration
            stop_flag (callable): Function to check if execution should stop
            advanced_conditions (dict): Advanced condition configurations
            
        Returns:
            tuple: (should_continue, error_message)
        """
        try:
            if not advanced_conditions:
                return True, None
            
            # Handle wait conditions
            wait_conditions = advanced_conditions.get('wait_conditions', [])
            for condition in wait_conditions:
                condition_type = condition.get('type', 'presence')
                
                if condition_type == 'presence':
                    result = self.presence_trigger.check_presence_trigger(condition)
                    if not result:
                        return False, f"Pre-sequence presence condition not met: {condition.get('image_path', 'unknown')}"
                
                elif condition_type == 'absence':
                    timeout = condition.get('timeout', 10)
                    result = self.absence_trigger.check_absence_trigger(condition, timeout)
                    if not result:
                        return False, f"Pre-sequence absence condition not met: {condition.get('image_path', 'unknown')}"
                
                elif condition_type == 'ocr':
                    result = self.ocr_trigger.check_ocr_trigger(condition, stop_flag=stop_flag)
                    if not result:
                        return False, f"Pre-sequence OCR condition not met: {condition.get('target_text', 'unknown')}"
            
            return True, None
            
        except Exception as e:
            logger.error(f"Error in pre-sequence conditions: {e}")
            return False, f"Exception in pre-sequence condition check: {e}"
    
    def handle_post_sequence_conditions(self, item, stop_flag, advanced_conditions):
        """
        Handle post-sequence conditions using modular components.
        
        Args:
            item (dict): Sequence item configuration
            stop_flag (callable): Function to check if execution should stop
            advanced_conditions (dict): Advanced condition configurations
            
        Returns:
            tuple: (should_continue, error_message)
        """
        try:
            if not advanced_conditions:
                return True, None
            
            # Handle wait conditions
            wait_conditions = advanced_conditions.get('wait_conditions', [])
            for condition in wait_conditions:
                condition_type = condition.get('type', 'presence')
                
                if condition_type == 'presence':
                    result = self.presence_trigger.check_presence_trigger(condition)
                    if not result:
                        return False, f"Post-sequence presence condition not met: {condition.get('image_path', 'unknown')}"
                
                elif condition_type == 'absence':
                    timeout = condition.get('timeout', 10)
                    result = self.absence_trigger.check_absence_trigger(condition, timeout)
                    if not result:
                        return False, f"Post-sequence absence condition not met: {condition.get('image_path', 'unknown')}"
                
                elif condition_type == 'ocr':
                    result = self.ocr_trigger.check_ocr_trigger(condition, stop_flag=stop_flag)
                    if not result:
                        return False, f"Post-sequence OCR condition not met: {condition.get('target_text', 'unknown')}"
            
            return True, None
            
        except Exception as e:
            logger.error(f"Error in post-sequence conditions: {e}")
            return False, f"Exception in post-sequence condition check: {e}"
    
    # Legacy method delegations for backward compatibility
    def _perform_single_presence_check(self, image_path, confidence, scales, trigger_config, screen_cv=None):
        """Legacy method - delegate to presence trigger"""
        return self.presence_trigger._perform_single_presence_check(image_path, confidence, scales, trigger_config, screen_cv)
    
    def _get_validation_cache_key(self, trigger_config):
        """Legacy method - delegate to validation cache with screen hash"""
        screen_hash = self.validation_cache.get_screen_hash(None, self.template_matcher)
        return self.validation_cache.get_validation_cache_key(trigger_config, screen_hash, self.path_resolver)
    
    def _invalidate_cache_if_screen_changed(self, screen_cv=None):
        """Legacy method - no longer needed with new caching system"""
        logger.debug("_invalidate_cache_if_screen_changed called - no action needed with new caching system")
        return True
    
    def _get_cached_validation_result(self, trigger_config, screen_cv=None):
        """Legacy method - delegate to validation cache"""
        return self.validation_cache.get_cached_validation_result(trigger_config, screen_cv, self.template_matcher, self.path_resolver)
    
    def _cache_validation_result(self, trigger_config, result, screen_cv=None):
        """Legacy method - delegate to validation cache"""
        return self.validation_cache.cache_validation_result(trigger_config, result, screen_cv, self.template_matcher, self.path_resolver)
