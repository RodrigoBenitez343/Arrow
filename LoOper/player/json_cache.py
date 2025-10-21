# json_cache.py
"""
JSON caching system for LoOper to eliminate redundant file loading.
Preloads entire chains and all referenced sequences for optimal performance.
"""

import json
import os
import time
from typing import Dict, Any, Optional, Set
from .config import logger


class JSONCache:
    """
    Centralized JSON cache for sequences and chains.
    Loads entire workflow graphs and all referenced sequences once at startup.
    """
    
    def __init__(self):
        self._sequence_cache: Dict[str, Dict[str, Any]] = {}
        self._chain_cache: Dict[str, Dict[str, Any]] = {}
        self._path_cache: Dict[str, str] = {}  # Cache resolved paths
        self._loaded_chains: Set[str] = set()  # Track loaded chains to prevent cycles
        
    def preload_chain(self, chain_file_path: str, chain_file_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Preload an entire chain and all its referenced sequences.
        
        Args:
            chain_file_path (str): Path to the chain file
            chain_file_dir (str, optional): Directory containing the chain file
            
        Returns:
            dict: The loaded chain configuration
        """
        start_time = time.time()
        
        # Resolve chain file path
        resolved_chain_path = self._resolve_path(chain_file_path, chain_file_dir)
        if not resolved_chain_path:
            raise FileNotFoundError(f"Chain file not found: {chain_file_path}")
        
        # Check if already loaded to prevent infinite recursion
        if resolved_chain_path in self._loaded_chains:
            logger.info(f"Chain already loaded: {resolved_chain_path}")
            return self._chain_cache[resolved_chain_path]
        
        # Load chain configuration
        logger.info(f"Preloading chain: {resolved_chain_path}")
        with open(resolved_chain_path, 'r', encoding='utf-8') as f:
            chain_config = json.load(f)
        
        # Cache the chain
        self._chain_cache[resolved_chain_path] = chain_config
        self._loaded_chains.add(resolved_chain_path)
        
        # Determine base directory for sequence resolution
        base_dir = os.path.dirname(resolved_chain_path) if resolved_chain_path else os.getcwd()
        
        # Preload all sequences referenced in this chain
        sequences_loaded = 0
        if isinstance(chain_config, dict) and 'sequences' in chain_config:
            for sequence_config in chain_config.get('sequences', []):
                seq_file = self._extract_sequence_file(sequence_config)
                if seq_file:
                    try:
                        self._preload_sequence(seq_file, base_dir)
                        sequences_loaded += 1
                    except Exception as e:
                        logger.warning(f"Failed to preload sequence {seq_file}: {e}")
        elif isinstance(chain_config, list):
            # Legacy format
            for sequence_config in chain_config:
                seq_file = self._extract_sequence_file(sequence_config)
                if seq_file:
                    try:
                        self._preload_sequence(seq_file, base_dir)
                        sequences_loaded += 1
                    except Exception as e:
                        logger.warning(f"Failed to preload sequence {seq_file}: {e}")
        
        # Preload nested chain imports
        chain_imports_loaded = 0
        if isinstance(chain_config, dict) and 'chain_import_nodes' in chain_config:
            for import_node in chain_config.get('chain_import_nodes', []):
                import_file = import_node.get('chain_file')
                if import_file:
                    try:
                        self.preload_chain(import_file, base_dir)
                        chain_imports_loaded += 1
                    except Exception as e:
                        logger.warning(f"Failed to preload imported chain {import_file}: {e}")
        
        load_time = time.time() - start_time
        logger.info(f"Chain preloading completed in {load_time:.3f}s: "
                   f"{sequences_loaded} sequences, {chain_imports_loaded} chain imports")
        
        return chain_config
    
    def _preload_sequence(self, sequence_file: str, base_dir: str) -> Dict[str, Any]:
        """
        Preload a single sequence file.
        
        Args:
            sequence_file (str): Sequence file name or path
            base_dir (str): Base directory for path resolution
            
        Returns:
            dict: The loaded sequence data
        """
        # Resolve sequence path
        resolved_path = self._resolve_sequence_path(sequence_file, base_dir)
        if not resolved_path:
            raise FileNotFoundError(f"Sequence file not found: {sequence_file}")
        
        # Check if already cached
        if resolved_path in self._sequence_cache:
            return self._sequence_cache[resolved_path]
        
        # Load and cache sequence
        logger.debug(f"Loading sequence: {resolved_path}")
        with open(resolved_path, 'r', encoding='utf-8') as f:
            sequence_data = json.load(f)
        
        if "actions" not in sequence_data:
            raise ValueError(f"Invalid sequence format in {resolved_path}: Missing actions")
        
        self._sequence_cache[resolved_path] = sequence_data
        logger.debug(f"Cached sequence with {len(sequence_data['actions'])} actions: {resolved_path}")
        
        return sequence_data
    
    def get_sequence(self, sequence_file: str, base_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get a cached sequence or load it if not cached.
        
        Args:
            sequence_file (str): Sequence file name or path
            base_dir (str, optional): Base directory for path resolution
            
        Returns:
            dict: The sequence data, or None if not found
        """
        resolved_path = self._resolve_sequence_path(sequence_file, base_dir)
        if not resolved_path:
            return None
        
        # Return from cache if available
        if resolved_path in self._sequence_cache:
            return self._sequence_cache[resolved_path].copy()  # Return copy to prevent modification
        
        # Load if not cached
        try:
            return self._preload_sequence(sequence_file, base_dir or os.getcwd())
        except Exception as e:
            logger.error(f"Failed to load sequence {sequence_file}: {e}")
            return None
    
    def get_chain(self, chain_file_path: str) -> Optional[Dict[str, Any]]:
        """
        Get a cached chain configuration.
        
        Args:
            chain_file_path (str): Path to the chain file
            
        Returns:
            dict: The chain configuration, or None if not found
        """
        resolved_path = self._resolve_path(chain_file_path)
        if resolved_path and resolved_path in self._chain_cache:
            return self._chain_cache[resolved_path].copy()  # Return copy to prevent modification
        return None
    
    def _resolve_sequence_path(self, sequence_file: str, base_dir: Optional[str] = None) -> Optional[str]:
        """
        Resolve sequence file path using multiple strategies.
        
        Args:
            sequence_file (str): Sequence file name or path
            base_dir (str, optional): Base directory for path resolution
            
        Returns:
            str: Resolved absolute path, or None if not found
        """
        # Check cache first
        cache_key = f"{sequence_file}:{base_dir or 'cwd'}"
        if cache_key in self._path_cache:
            return self._path_cache[cache_key]
        
        # Extract sequence file from name field if needed
        original_seq_file = sequence_file
        if ': ' in sequence_file:
            sequence_file = sequence_file.split(': ', 1)[1]
        
        # Handle numbered sequence names (e.g., "1.json 2" -> "1.json")
        import re
        if sequence_file and re.match(r'^(.+\.json)\s+\d+$', sequence_file):
            sequence_file = re.match(r'^(.+\.json)\s+\d+$', sequence_file).group(1)
        
        # Determine base directory
        base_dir = base_dir or os.getcwd()
        
        # Try multiple path resolution strategies
        possible_paths = [
            sequence_file,  # Original path
            os.path.join(base_dir, sequence_file),  # Relative to base dir
            os.path.join(base_dir, 'sequences', sequence_file),  # sequences subdirectory in base dir
            os.path.join('sequences', sequence_file),  # sequences subdirectory in cwd
            os.path.join(os.getcwd(), sequence_file),  # current working directory
            os.path.join(os.getcwd(), 'sequences', sequence_file),  # cwd/sequences
        ]
        
        # Add .json extension if missing
        if not sequence_file.endswith('.json'):
            possible_paths.extend([
                sequence_file + '.json',
                os.path.join(base_dir, sequence_file + '.json'),
                os.path.join(base_dir, 'sequences', sequence_file + '.json'),
                os.path.join('sequences', sequence_file + '.json'),
                os.path.join(os.getcwd(), sequence_file + '.json'),
                os.path.join(os.getcwd(), 'sequences', sequence_file + '.json'),
            ])
        
        # Find the first existing file
        for path in possible_paths:
            if os.path.exists(path):
                resolved_path = os.path.abspath(path)
                self._path_cache[cache_key] = resolved_path
                return resolved_path
        
        logger.error(f"Sequence file not found: '{original_seq_file}' (searched {len(possible_paths)} paths)")
        return None
    
    def _resolve_path(self, file_path: str, base_dir: Optional[str] = None) -> Optional[str]:
        """
        Resolve a general file path.
        
        Args:
            file_path (str): File path to resolve
            base_dir (str, optional): Base directory for path resolution
            
        Returns:
            str: Resolved absolute path, or None if not found
        """
        if os.path.isabs(file_path) and os.path.exists(file_path):
            return os.path.abspath(file_path)
        
        if base_dir:
            full_path = os.path.join(base_dir, file_path)
            if os.path.exists(full_path):
                return os.path.abspath(full_path)
        
        if os.path.exists(file_path):
            return os.path.abspath(file_path)
        
        return None
    
    def _extract_sequence_file(self, sequence_config: Dict[str, Any]) -> Optional[str]:
        """
        Extract sequence file name from sequence configuration.
        
        Args:
            sequence_config (dict): Sequence configuration
            
        Returns:
            str: Sequence file name, or None if not found
        """
        seq_file = sequence_config.get('sequence_file')
        if not seq_file:
            name = sequence_config.get('name', '')
            if ': ' in name:
                seq_file = name.split(': ', 1)[1]
            else:
                seq_file = name
        
        return seq_file
    
    def clear_cache(self):
        """Clear all cached data."""
        self._sequence_cache.clear()
        self._chain_cache.clear()
        self._path_cache.clear()
        self._loaded_chains.clear()
        logger.info("JSON cache cleared")
    
    def get_cache_stats(self) -> Dict[str, int]:
        """
        Get cache statistics.
        
        Returns:
            dict: Cache statistics
        """
        return {
            'sequences_cached': len(self._sequence_cache),
            'chains_cached': len(self._chain_cache),
            'paths_cached': len(self._path_cache)
        }


# Global cache instance
_global_cache = JSONCache()


def get_cache() -> JSONCache:
    """Get the global JSON cache instance."""
    return _global_cache


def preload_chain(chain_file_path: str, chain_file_dir: Optional[str] = None) -> Dict[str, Any]:
    """Convenience function to preload a chain using the global cache."""
    return _global_cache.preload_chain(chain_file_path, chain_file_dir)


def get_sequence(sequence_file: str, base_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Convenience function to get a sequence using the global cache."""
    return _global_cache.get_sequence(sequence_file, base_dir)


def get_chain(chain_file_path: str) -> Optional[Dict[str, Any]]:
    """Convenience function to get a chain using the global cache."""
    return _global_cache.get_chain(chain_file_path)