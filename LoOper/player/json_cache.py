import logging
logger = logging.getLogger(__name__)
# json_cache.py
"""
JSON caching system for LoOper to eliminate redundant file loading.
Preloads entire chains and all referenced sequences for optimal performance.
"""

import json
import os
import sys
import time
from typing import Dict, Any, Optional, Set


def _is_sequence_shape(path: str) -> bool:
    """Return True when the JSON file at *path* has sequence shape.

    A valid sequence file carries an ``actions`` list.  Chain files
    (``chains/*.json``) carry chain-only fields and no ``actions`` — they
    must never be accepted as sequences.  This check prevents the
    chains/CLOSE.json-vs-sequences/CLOSE.json collision during preload.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get("actions"), list)


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
        self._sequence_mtimes: Dict[str, float] = {}  # Track modification times
        self._chain_mtimes: Dict[str, float] = {}     # Track modification times
        
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
            # Also check if the file on disk is newer than our cached version
            try:
                current_mtime = os.path.getmtime(resolved_chain_path)
                if current_mtime <= self._chain_mtimes.get(resolved_chain_path, 0):
                    logger.info(f"Chain already loaded and up-to-date: {resolved_chain_path}")
                    return self._chain_cache[resolved_chain_path]
                logger.info(f"Chain file modified on disk, reloading: {resolved_chain_path}")
            except (OSError, IOError):
                # If we can't check mtime, assume it's okay to use cache or reload if needed
                if resolved_chain_path in self._chain_cache:
                    return self._chain_cache[resolved_chain_path]
        
        # Load chain configuration
        logger.info(f"Preloading chain: {resolved_chain_path}")
        with open(resolved_chain_path, 'r', encoding='utf-8') as f:
            chain_config = json.load(f)
        
        # Cache the chain and its modification time
        self._chain_cache[resolved_chain_path] = chain_config
        try:
            self._chain_mtimes[resolved_chain_path] = os.path.getmtime(resolved_chain_path)
        except (OSError, IOError):
            self._chain_mtimes[resolved_chain_path] = 0
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
            # Check if file has been modified since caching
            try:
                current_mtime = os.path.getmtime(resolved_path)
                if current_mtime <= self._sequence_mtimes.get(resolved_path, 0):
                    return self._sequence_cache[resolved_path]
                logger.info(f"Sequence file modified on disk, reloading: {resolved_path}")
            except (OSError, IOError):
                # If we can't check mtime, assume cache is okay
                return self._sequence_cache[resolved_path]
        
        # Load and cache sequence
        logger.debug(f"Loading sequence: {resolved_path}")
        with open(resolved_path, 'r', encoding='utf-8') as f:
            sequence_data = json.load(f)
        
        if "actions" not in sequence_data:
            raise ValueError(f"Invalid sequence format in {resolved_path}: Missing actions")
        
        self._sequence_cache[resolved_path] = sequence_data
        try:
            self._sequence_mtimes[resolved_path] = os.path.getmtime(resolved_path)
        except (OSError, IOError):
            self._sequence_mtimes[resolved_path] = 0
            
        logger.debug(f"Cached sequence with {len(sequence_data['actions'])} actions: {resolved_path}")
        
        return sequence_data
    
    def get_sequence(self, sequence_file: str, base_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get a sequence from cache or load it if not cached.
        
        Args:
            sequence_file (str): Sequence file name or path
            base_dir (str, optional): Base directory for path resolution
            
        Returns:
            dict: The loaded sequence configuration, or None if not found
        """
        resolved_path = self._resolve_sequence_path(sequence_file, base_dir)
        if not resolved_path:
            return None
        
        # Check cache
        cache_key = resolved_path
        if cache_key in self._sequence_cache:
            # Check if file has been modified since caching
            try:
                current_mtime = os.path.getmtime(resolved_path)
                if current_mtime <= self._sequence_mtimes.get(cache_key, 0):
                    logger.debug(f"Using cached sequence: {cache_key}")
                    return self._sequence_cache[cache_key].copy()
                logger.info(f"Sequence file modified on disk, reloading: {cache_key}")
            except (OSError, IOError):
                # If we can't check mtime, assume cache is okay
                return self._sequence_cache[cache_key].copy()
        
        # Load from file
        logger.debug(f"Loading sequence from disk: {resolved_path}")
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                sequence_data = json.load(f)
            
            if "actions" not in sequence_data:
                raise ValueError(f"Invalid sequence format in {resolved_path}: Missing actions")
            
            # Cache the data and modification time
            self._sequence_cache[cache_key] = sequence_data
            try:
                self._sequence_mtimes[cache_key] = os.path.getmtime(resolved_path)
            except (OSError, IOError):
                self._sequence_mtimes[cache_key] = 0
            
            return sequence_data.copy()
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

        base_dir_name = os.path.basename(os.path.normpath(base_dir)).lower() if base_dir else ""
        is_base_dir_chains = base_dir_name == "chains"
        is_bare_filename = (
            bool(sequence_file)
            and os.path.basename(sequence_file) == sequence_file
            and ("/" not in sequence_file and "\\" not in sequence_file)
        )
        
        # Determine executable directory for frozen builds
        exe_dir = None
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
        _meipass = getattr(sys, '_MEIPASS', None)
        
        # Try multiple path resolution strategies
        looper_sequences = os.path.join(os.getcwd(), 'LoOper', 'sequences')

        possible_paths = [sequence_file]  # Original path (may be absolute)

        # Frozen builds: bundled sequences live under _MEIPASS or next to
        # the exe — search THOSE FIRST (with shape validation at selection
        # time) so a chain-shaped chains/<name>.json can never win over
        # the real sequence file.
        if is_bare_filename:
            _frozen_seq_candidates = []
            if exe_dir:
                _frozen_seq_candidates.extend([
                    os.path.join(exe_dir, 'LoOper', 'sequences', sequence_file),
                    os.path.join(exe_dir, '_internal', 'LoOper', 'sequences', sequence_file),
                    os.path.join(exe_dir, '_internal', 'sequences', sequence_file),
                    os.path.join(exe_dir, 'sequences', sequence_file),
                ])
            if _meipass:
                _frozen_seq_candidates.extend([
                    os.path.join(_meipass, 'LoOper', 'sequences', sequence_file),
                    os.path.join(_meipass, 'sequences', sequence_file),
                ])
            possible_paths = _frozen_seq_candidates + possible_paths

        if is_bare_filename:
            possible_paths.extend([
                os.path.join(base_dir, 'sequences', sequence_file),  # sequences next to chain
                os.path.join(os.getcwd(), 'sequences', sequence_file),  # cwd/sequences
                os.path.join('sequences', sequence_file),  # sequences relative to cwd
                os.path.join(looper_sequences, sequence_file),  # LoOper/sequences
            ])
            if not is_base_dir_chains:
                possible_paths.append(os.path.join(base_dir, sequence_file))  # Relative to base dir
            possible_paths.append(os.path.join(os.getcwd(), sequence_file))  # current working directory
            if is_base_dir_chains:
                possible_paths.append(os.path.join(base_dir, sequence_file))  # last-resort: avoid chains/name.json collisions
        else:
            possible_paths.extend([
                os.path.join(base_dir, sequence_file),  # Relative to base dir
                os.path.join(base_dir, 'sequences', sequence_file),  # sequences subdirectory in base dir
                os.path.join('sequences', sequence_file),  # sequences subdirectory in cwd
                os.path.join(os.getcwd(), sequence_file),  # current working directory
                os.path.join(os.getcwd(), 'sequences', sequence_file),  # cwd/sequences
                os.path.join(looper_sequences, sequence_file),  # LoOper/sequences
            ])
        
        # Add frozen build paths if applicable
        if exe_dir:
            possible_paths.extend([
                os.path.join(exe_dir, sequence_file),
                os.path.join(exe_dir, 'sequences', sequence_file),
            ])

        # One-file frozen build: bundled data is extracted to sys._MEIPASS
        # (a temp dir), NOT next to the exe.  Exported agents bundle
        # sequences under _MEIPASS/LoOper/sequences, so search there too.
        _meipass = getattr(sys, '_MEIPASS', None)
        if _meipass:
            possible_paths.extend([
                os.path.join(_meipass, sequence_file),
                os.path.join(_meipass, 'sequences', sequence_file),
                os.path.join(_meipass, 'LoOper', 'sequences', sequence_file),
            ])
        possible_paths.append(os.path.join(os.getcwd(), 'LoOper', 'sequences', sequence_file))
        
        # Add .json extension if missing
        if not sequence_file.endswith('.json'):
            json_name = sequence_file + '.json'

            possible_paths.append(json_name)
            if is_bare_filename:
                possible_paths.extend([
                    os.path.join(base_dir, 'sequences', json_name),
                    os.path.join(os.getcwd(), 'sequences', json_name),
                    os.path.join('sequences', json_name),
                    os.path.join(looper_sequences, json_name),
                ])
                if not is_base_dir_chains:
                    possible_paths.append(os.path.join(base_dir, json_name))
                possible_paths.append(os.path.join(os.getcwd(), json_name))
                if is_base_dir_chains:
                    possible_paths.append(os.path.join(base_dir, json_name))
            else:
                possible_paths.extend([
                    os.path.join(base_dir, json_name),
                    os.path.join(base_dir, 'sequences', json_name),
                    os.path.join('sequences', json_name),
                    os.path.join(os.getcwd(), json_name),
                    os.path.join(os.getcwd(), 'sequences', json_name),
                    os.path.join(looper_sequences, json_name),
                    os.path.join(os.getcwd(), 'LoOper', 'sequences', json_name),
                ])
            if exe_dir and is_bare_filename:
                possible_paths.extend([
                    os.path.join(exe_dir, 'LoOper', 'sequences', json_name),
                    os.path.join(exe_dir, 'sequences', json_name),
                ])
            if _meipass and is_bare_filename:
                possible_paths.extend([
                    os.path.join(_meipass, 'LoOper', 'sequences', json_name),
                    os.path.join(_meipass, 'sequences', json_name),
                ])
        nested_sequences = os.path.join(os.getcwd(), 'sequences', 'sequences')
        possible_paths.extend([
            os.path.join(nested_sequences, sequence_file),
            os.path.join(nested_sequences, sequence_file + '.json') if not sequence_file.endswith('.json') else os.path.join(nested_sequences, sequence_file),
        ])
        
        if exe_dir:
            nested_exe = os.path.join(exe_dir, 'sequences', 'sequences')
            possible_paths.extend([
                os.path.join(nested_exe, sequence_file),
                os.path.join(nested_exe, sequence_file + '.json') if not sequence_file.endswith('.json') else os.path.join(nested_exe, sequence_file),
            ])
        
        # Find the first existing file with SEQUENCE SHAPE.  A file that
        # exists but is chain-shaped (e.g. chains/CLOSE.json with no
        # 'actions' list) is never accepted as a sequence — the search
        # continues so the real sequences/CLOSE.json wins.
        for path in possible_paths:
            if os.path.exists(path):
                if _is_sequence_shape(path):
                    resolved_path = os.path.abspath(path)
                    self._path_cache[cache_key] = resolved_path
                    return resolved_path
                logger.warning(
                    f"Ignoring non-sequence file at {path} (chain-shaped JSON)"
                )
        
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
    
    def invalidate_file(self, file_path: str):
        """
        Invalidate cache for a specific file and all chains that depend on it.
        
        Args:
            file_path (str): Path to the file that was modified
        """
        resolved_path = os.path.abspath(file_path) if file_path else None
        if not resolved_path:
            return
            
        logger.info(f"Invalidating cache for file: {resolved_path}")
        
        # Remove from sequence cache if it's a sequence
        if resolved_path in self._sequence_cache:
            del self._sequence_cache[resolved_path]
            logger.debug(f"Removed sequence from cache: {resolved_path}")
        
        # Remove from chain cache if it's a chain
        if resolved_path in self._chain_cache:
            del self._chain_cache[resolved_path]
            self._loaded_chains.discard(resolved_path)
            logger.debug(f"Removed chain from cache: {resolved_path}")
        
        # Find and invalidate all chains that depend on this file
        self._invalidate_dependent_chains(resolved_path)
        
        # Clear path cache entries that might reference this file
        keys_to_remove = []
        for key, cached_path in self._path_cache.items():
            if cached_path == resolved_path:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del self._path_cache[key]
            logger.debug(f"Removed path cache entry: {key}")
    
    def _invalidate_dependent_chains(self, modified_file_path: str):
        """
        Recursively invalidate all chains that depend on the modified file.
        
        Args:
            modified_file_path (str): Path to the file that was modified
        """
        chains_to_invalidate = []
        
        # Check all cached chains for dependencies on the modified file
        for chain_path, chain_config in self._chain_cache.items():
            if self._chain_depends_on_file(chain_config, modified_file_path, os.path.dirname(chain_path)):
                chains_to_invalidate.append(chain_path)
        
        # Invalidate dependent chains
        for chain_path in chains_to_invalidate:
            logger.info(f"Invalidating dependent chain: {chain_path}")
            del self._chain_cache[chain_path]
            self._loaded_chains.discard(chain_path)
            
            # Recursively invalidate chains that depend on this chain
            self._invalidate_dependent_chains(chain_path)
    
    def _chain_depends_on_file(self, chain_config: Dict[str, Any], file_path: str, base_dir: str) -> bool:
        """
        Check if a chain configuration depends on a specific file.
        
        Args:
            chain_config (dict): Chain configuration to check
            file_path (str): File path to check for dependency
            base_dir (str): Base directory for resolving relative paths
            
        Returns:
            bool: True if the chain depends on the file
        """
        if not isinstance(chain_config, dict):
            return False
        
        # Check sequence dependencies
        for sequence_config in chain_config.get('sequences', []):
            seq_file = self._extract_sequence_file(sequence_config)
            if seq_file:
                resolved_seq_path = self._resolve_sequence_path(seq_file, base_dir)
                if resolved_seq_path and os.path.abspath(resolved_seq_path) == os.path.abspath(file_path):
                    return True
        
        # Check chain import dependencies
        for import_node in chain_config.get('chain_import_nodes', []):
            import_file = import_node.get('chain_file')
            if import_file:
                resolved_import_path = self._resolve_path(import_file, base_dir)
                if resolved_import_path and os.path.abspath(resolved_import_path) == os.path.abspath(file_path):
                    return True
        
        return False
    
    def invalidate_directory(self, directory_path: str):
        """
        Invalidate cache for all files in a directory.
        
        Args:
            directory_path (str): Path to the directory
        """
        if not os.path.isdir(directory_path):
            return
            
        abs_dir_path = os.path.abspath(directory_path)
        logger.info(f"Invalidating cache for directory: {abs_dir_path}")
        
        # Find all cached files in this directory
        files_to_invalidate = []
        
        for cached_path in self._sequence_cache.keys():
            if os.path.dirname(os.path.abspath(cached_path)) == abs_dir_path:
                files_to_invalidate.append(cached_path)
        
        for cached_path in self._chain_cache.keys():
            if os.path.dirname(os.path.abspath(cached_path)) == abs_dir_path:
                files_to_invalidate.append(cached_path)
        
        # Invalidate each file
        for file_path in files_to_invalidate:
            self.invalidate_file(file_path)
    
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


def invalidate_file(file_path: str):
    """Convenience function to invalidate cache for a specific file."""
    return _global_cache.invalidate_file(file_path)


def invalidate_directory(directory_path: str):
    """Convenience function to invalidate cache for all files in a directory."""
    return _global_cache.invalidate_directory(directory_path)


def clear_cache():
    """Convenience function to clear the entire cache."""
    return _global_cache.clear_cache()
