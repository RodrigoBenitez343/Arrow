import logging
logger = logging.getLogger(__name__)
# path_resolver.py
"""
Path resolver module for conditional fallback handler.
Handles resolution of relative and absolute paths for sequence files and images.
"""
import os


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
            
            # Add LoOper/sequences folder paths
            looper_sequences = os.path.join(os.getcwd(), 'LoOper', 'sequences')
            candidates.append(os.path.join(looper_sequences, path))
            if not path.lower().endswith('.json'):
                candidates.append(os.path.join(looper_sequences, path + '.json'))
            candidates.append(os.path.join(os.getcwd(), 'LoOper', 'sequences', path))
            if not path.lower().endswith('.json'):
                candidates.append(os.path.join(os.getcwd(), 'LoOper', 'sequences', path + '.json'))
                
        # Current working directory fallbacks
        candidates.append(os.path.join(os.getcwd(), path))
        if add_json and not path.lower().endswith('.json'):
            candidates.append(os.path.join(os.getcwd(), path + '.json'))

        # Frozen / cross-machine fallback: an absolute path stored on the
        # authoring machine (e.g. D:\LoOperV2\...\screenshots\x.png) will not
        # exist here.  In a one-file PyInstaller build bundled assets are
        # extracted to sys._MEIPASS, so search for the basename under the known
        # bundled asset directories relative to _MEIPASS / exe dir / cwd.
        candidates.extend(self._bundled_basename_candidates(path, add_json))

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

    def _bundled_basename_candidates(self, path, add_json=False):
        """Build basename-based candidates under bundled asset roots.

        Handles the exported-agent case where a stored absolute path from the
        authoring machine is invalid, but the file was bundled (extracted to
        sys._MEIPASS for one-file builds, or next to the exe / cwd).
        """
        import sys as _sys
        out = []
        base_name = os.path.basename(path)
        if not base_name:
            return out
        roots = [os.getcwd()]
        meipass = getattr(_sys, '_MEIPASS', None)
        if meipass:
            roots.append(meipass)
        if getattr(_sys, 'frozen', False):
            roots.append(os.path.dirname(_sys.executable))
        subdirs = [
            '',
            'screenshots',
            os.path.join('sequences', 'screenshots'),
            os.path.join('LoOper', 'sequences', 'screenshots'),
            'sequences',
            os.path.join('LoOper', 'sequences'),
        ]
        names = [base_name]
        if add_json and not base_name.lower().endswith('.json'):
            names.append(base_name + '.json')
        for root in roots:
            for sub in subdirs:
                target_dir = os.path.join(root, sub) if sub else root
                for nm in names:
                    out.append(os.path.join(target_dir, nm))
        return out

    def resolve_code_path(self, path):
        """
        Resolve a code file path (e.g. .py scripts).
        Checks common code directories like 'scripts', 'code', etc.
        
        Args:
            path (str): The path to resolve
            
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
        
        # Original path
        candidates.append(path)
        candidates.append(os.path.join(base_dir, path))
        
        # Common subdirectories for code
        for subdir in ['scripts', 'code', 'python', 'custom_nodes']:
            candidates.append(os.path.join(base_dir, subdir, path))
            candidates.append(os.path.join(os.getcwd(), subdir, path))
            candidates.append(os.path.join(os.getcwd(), 'LoOper', subdir, path))
            
        # Current working directory fallback
        candidates.append(os.path.join(os.getcwd(), path))

        # Frozen / cross-machine fallback (bundled under sys._MEIPASS etc.)
        candidates.extend(self._bundled_basename_candidates(path))
        
        # Return the first existing candidate
        for candidate in candidates:
            try:
                if os.path.exists(candidate):
                    logger.debug(f"Resolved code path '{path}' to '{candidate}'")
                    return candidate
            except Exception:
                continue
                
        logger.debug(f"Could not resolve code path '{path}', returning original")
        return path
    
    def resolve_node_paths(self, node):
        """
        Resolve all paths within a node structure.
        Supports CodeNode, conditional nodes, etc.
        
        Args:
            node (dict): The node to resolve
            
        Returns:
            dict: Map of field names to resolved paths. Returns empty dict if no paths found or resolved.
        """
        resolved = {}
        data = node.get('data', {})
        node_type = node.get('type') or data.get('type')
        
        # Handle CodeNode
        if node_type == 'CodeNode':
            file_path = data.get('file_path')
            if file_path:
                resolved['file_path'] = self.resolve_code_path(file_path)
                
        # Handle Conditional Nodes
        trigger_type = data.get('trigger_type') or data.get('condition_type')
        if trigger_type:
            if trigger_type in ['presence', 'absence']:
                image_path = data.get('image_path')
                if image_path:
                    resolved['image_path'] = self.resolve_path(image_path)
            elif trigger_type == 'loop':
                seq_file = data.get('sequence_file')
                if seq_file:
                    resolved['sequence_file'] = self.resolve_path(seq_file, add_json=True)
            elif trigger_type == 'ocr':
                # OCR might have image path if it's image-based OCR (not screen)
                image_path = data.get('image_path')
                if image_path:
                    resolved['image_path'] = self.resolve_path(image_path)
                    
        return resolved

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