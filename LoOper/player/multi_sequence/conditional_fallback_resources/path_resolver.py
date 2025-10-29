# path_resolver.py
"""
Path resolver module for conditional fallback handler.
Handles resolution of relative and absolute paths for sequence files and images.
"""
import os
from ...config import logger


class PathResolver:
    """Handles path resolution for sequence files and images"""
    
    def __init__(self, chain_file_dir=None):
        """
        Initialize path resolver with base directory.
        
        Args:
            chain_file_dir (str): Base directory of the current chain file
        """
        self.chain_file_dir = chain_file_dir or os.getcwd()
    
    def resolve_path(self, path, add_json=False):
        """
        Resolve a possibly relative path against the chain file directory and cwd.
        When add_json=True, also consider .json variants and common 'sequences' subdirectories.
        
        Args:
            path (str): The path to resolve
            add_json (bool): Whether to consider .json variants
            
        Returns:
            str: Resolved path
        """
        if not path:
            return path
            
        # If the path already exists as given, return it
        try:
            if os.path.isabs(path) and os.path.exists(path):
                return path
        except Exception:
            pass
            
        base_dir = self.chain_file_dir or os.getcwd()
        candidates = []
        
        # Original path (relative)
        candidates.append(path)
        if add_json and not path.lower().endswith('.json'):
            candidates.append(path + '.json')
            
        # Relative to chain file directory
        candidates.append(os.path.join(base_dir, path))
        if add_json and not path.lower().endswith('.json'):
            candidates.append(os.path.join(base_dir, path + '.json'))
            
        # Common subdirectories for sequences
        if add_json:
            candidates.append(os.path.join(base_dir, 'sequences', path))
            if not path.lower().endswith('.json'):
                candidates.append(os.path.join(base_dir, 'sequences', path + '.json'))
            candidates.append(os.path.join('sequences', path))
            if not path.lower().endswith('.json'):
                candidates.append(os.path.join('sequences', path + '.json'))
                
        # Current working directory fallbacks
        candidates.append(os.path.join(os.getcwd(), path))
        if add_json and not path.lower().endswith('.json'):
            candidates.append(os.path.join(os.getcwd(), path + '.json'))
            
        # Return the first existing candidate
        for candidate in candidates:
            try:
                if os.path.exists(candidate):
                    logger.debug(f"Resolved path '{path}' to '{candidate}'")
                    return candidate
            except Exception:
                continue
                
        logger.debug(f"Could not resolve path '{path}', returning original")
        return path
    
    def set_chain_file_dir(self, chain_file_dir):
        """
        Update the base chain file directory.
        
        Args:
            chain_file_dir (str): New base directory
        """
        self.chain_file_dir = chain_file_dir
        logger.debug(f"Updated chain file directory to: {chain_file_dir}")
    
    def get_chain_file_dir(self):
        """
        Get the current chain file directory.
        
        Returns:
            str: Current chain file directory
        """
        return self.chain_file_dir