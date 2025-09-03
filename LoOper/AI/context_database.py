#!/usr/bin/env python3
"""
Context Database Module

Provides persistent storage for context data used by ComoRAG integration.
Stores screenshots, action history, and other context data for use in LLM nodes.
"""

import os
import json
import time
import sqlite3
import base64
import logging
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

class ContextDatabase:
    """Database for storing context data for ComoRAG integration"""
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialize the context database
        
        Args:
            db_path: Path to the SQLite database file. If None, uses default location.
        """
        if db_path is None:
            # Use default location in project directory
            project_root = Path(__file__).parent.parent
            db_dir = project_root / 'data'
            os.makedirs(db_dir, exist_ok=True)
            db_path = str(db_dir / 'context.db')
        
        self.db_path = db_path
        self.conn = None
        self.initialize_database()
    
    def initialize_database(self):
        """Initialize the database schema if it doesn't exist"""
        try:
            self.conn = sqlite3.connect(self.db_path)
            cursor = self.conn.cursor()
            
            # Create context_items table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS context_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT,
                content_type TEXT,
                source TEXT,
                timestamp REAL,
                metadata TEXT,
                chain_id TEXT,
                sequence_id TEXT,
                node_id TEXT,
                image_id INTEGER,
                FOREIGN KEY (image_id) REFERENCES context_images(id)
            )
            """)
            
            # Create context_images table for storing images
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS context_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_data BLOB,
                image_path TEXT,
                image_type TEXT,
                timestamp REAL,
                metadata TEXT
            )
            """)
            
            # Create index on timestamp for faster queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_timestamp ON context_items(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_chain ON context_items(chain_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_context_sequence ON context_items(sequence_id)")
            
            self.conn.commit()
            logger.info(f"Initialized context database at {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}")
            if self.conn:
                self.conn.close()
                self.conn = None
    
    def _ensure_connection(self):
        """Ensure database connection is active"""
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
    
    def add_context_item(self, content: str, content_type: str, source: str, 
                         chain_id: Optional[str] = None, sequence_id: Optional[str] = None,
                         node_id: Optional[str] = None, metadata: Optional[Dict] = None,
                         timestamp: Optional[float] = None) -> int:
        """Add a context item to the database
        
        Args:
            content: The content text
            content_type: Type of content (e.g., 'ocr', 'llm_output', 'action')
            source: Source of the content (e.g., 'screenshot', 'user_input')
            chain_id: ID of the chain this context belongs to
            sequence_id: ID of the sequence this context belongs to
            node_id: ID of the node this context belongs to
            metadata: Additional metadata as a dictionary
            timestamp: Timestamp for the context item (defaults to current time)
            
        Returns:
            ID of the inserted context item
        """
        try:
            self._ensure_connection()
            cursor = self.conn.cursor()
            
            if timestamp is None:
                timestamp = time.time()
            
            metadata_json = json.dumps(metadata) if metadata else None
            
            cursor.execute("""
            INSERT INTO context_items 
            (content, content_type, source, timestamp, metadata, chain_id, sequence_id, node_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (content, content_type, source, timestamp, metadata_json, chain_id, sequence_id, node_id))
            
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"Error adding context item: {e}")
            return -1
    
    def add_image(self, image_data: Optional[bytes] = None, image_path: Optional[str] = None, 
                  image_type: str = 'screenshot', metadata: Optional[Dict] = None,
                  timestamp: Optional[float] = None) -> int:
        """Add an image to the database
        
        Args:
            image_data: Raw image data as bytes
            image_path: Path to the image file
            image_type: Type of image (e.g., 'screenshot', 'template')
            metadata: Additional metadata as a dictionary
            timestamp: Timestamp for the image (defaults to current time)
            
        Returns:
            ID of the inserted image
        """
        try:
            self._ensure_connection()
            cursor = self.conn.cursor()
            
            if timestamp is None:
                timestamp = time.time()
            
            # If image_data is not provided but image_path is, read the image file
            if image_data is None and image_path and os.path.exists(image_path):
                with open(image_path, 'rb') as f:
                    image_data = f.read()
            
            metadata_json = json.dumps(metadata) if metadata else None
            
            cursor.execute("""
            INSERT INTO context_images 
            (image_data, image_path, image_type, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?)
            """, (image_data, image_path, image_type, timestamp, metadata_json))
            
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"Error adding image: {e}")
            return -1
    
    def add_context_with_image(self, content: str, content_type: str, source: str,
                              image_data: Optional[bytes] = None, image_path: Optional[str] = None,
                              chain_id: Optional[str] = None, sequence_id: Optional[str] = None,
                              node_id: Optional[str] = None, metadata: Optional[Dict] = None,
                              timestamp: Optional[float] = None) -> Tuple[int, int]:
        """Add a context item with an associated image
        
        Args:
            content: The content text
            content_type: Type of content
            source: Source of the content
            image_data: Raw image data as bytes
            image_path: Path to the image file
            chain_id: ID of the chain this context belongs to
            sequence_id: ID of the sequence this context belongs to
            node_id: ID of the node this context belongs to
            metadata: Additional metadata as a dictionary
            timestamp: Timestamp for the context item (defaults to current time)
            
        Returns:
            Tuple of (context_id, image_id)
        """
        try:
            self._ensure_connection()
            
            if timestamp is None:
                timestamp = time.time()
            
            # First add the image
            image_id = self.add_image(image_data, image_path, 'screenshot', metadata, timestamp)
            
            if image_id < 0:
                return -1, -1
            
            # Then add the context item with reference to the image
            cursor = self.conn.cursor()
            metadata_json = json.dumps(metadata) if metadata else None
            
            cursor.execute("""
            INSERT INTO context_items 
            (content, content_type, source, timestamp, metadata, chain_id, sequence_id, node_id, image_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (content, content_type, source, timestamp, metadata_json, 
                  chain_id, sequence_id, node_id, image_id))
            
            self.conn.commit()
            context_id = cursor.lastrowid
            
            return context_id, image_id
        except sqlite3.Error as e:
            logger.error(f"Error adding context with image: {e}")
            return -1, -1
    
    def get_context_items(self, limit: int = 100, offset: int = 0, 
                         chain_id: Optional[str] = None, sequence_id: Optional[str] = None,
                         node_id: Optional[str] = None, content_type: Optional[str] = None,
                         source: Optional[str] = None, start_time: Optional[float] = None,
                         end_time: Optional[float] = None) -> List[Dict]:
        """Get context items with optional filtering
        
        Args:
            limit: Maximum number of items to return
            offset: Offset for pagination
            chain_id: Filter by chain ID
            sequence_id: Filter by sequence ID
            node_id: Filter by node ID
            content_type: Filter by content type
            source: Filter by source
            start_time: Filter by start timestamp
            end_time: Filter by end timestamp
            
        Returns:
            List of context items as dictionaries
        """
        try:
            self._ensure_connection()
            cursor = self.conn.cursor()
            
            query = "SELECT * FROM context_items"
            params = []
            where_clauses = []
            
            if chain_id is not None:
                where_clauses.append("chain_id = ?")
                params.append(chain_id)
            
            if sequence_id is not None:
                where_clauses.append("sequence_id = ?")
                params.append(sequence_id)
            
            if node_id is not None:
                where_clauses.append("node_id = ?")
                params.append(node_id)
            
            if content_type is not None:
                where_clauses.append("content_type = ?")
                params.append(content_type)
            
            if source is not None:
                where_clauses.append("source = ?")
                params.append(source)
            
            if start_time is not None:
                where_clauses.append("timestamp >= ?")
                params.append(start_time)
            
            if end_time is not None:
                where_clauses.append("timestamp <= ?")
                params.append(end_time)
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            
            query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            result = []
            for row in rows:
                item = {
                    'id': row[0],
                    'content': row[1],
                    'content_type': row[2],
                    'source': row[3],
                    'timestamp': row[4],
                    'metadata': json.loads(row[5]) if row[5] else None,
                    'chain_id': row[6],
                    'sequence_id': row[7],
                    'node_id': row[8],
                    'image_id': row[9]
                }
                result.append(item)
            
            return result
        except sqlite3.Error as e:
            logger.error(f"Error getting context items: {e}")
            return []
    
    def get_image(self, image_id: int) -> Optional[Dict]:
        """Get an image by ID
        
        Args:
            image_id: ID of the image to retrieve
            
        Returns:
            Image data as a dictionary or None if not found
        """
        try:
            self._ensure_connection()
            cursor = self.conn.cursor()
            
            cursor.execute("SELECT * FROM context_images WHERE id = ?", (image_id,))
            row = cursor.fetchone()
            
            if row is None:
                return None
            
            return {
                'id': row[0],
                'image_data': row[1],
                'image_path': row[2],
                'image_type': row[3],
                'timestamp': row[4],
                'metadata': json.loads(row[5]) if row[5] else None
            }
        except sqlite3.Error as e:
            logger.error(f"Error getting image: {e}")
            return None
    
    def get_context_with_images(self, limit: int = 100, offset: int = 0,
                               chain_id: Optional[str] = None, sequence_id: Optional[str] = None,
                               node_id: Optional[str] = None) -> List[Dict]:
        """Get context items with their associated images
        
        Args:
            limit: Maximum number of items to return
            offset: Offset for pagination
            chain_id: Filter by chain ID
            sequence_id: Filter by sequence ID
            node_id: Filter by node ID
            
        Returns:
            List of context items with image data
        """
        try:
            self._ensure_connection()
            cursor = self.conn.cursor()
            
            query = """
            SELECT c.*, i.image_data, i.image_path, i.image_type, i.metadata as image_metadata
            FROM context_items c
            LEFT JOIN context_images i ON c.image_id = i.id
            """
            
            params = []
            where_clauses = []
            
            if chain_id is not None:
                where_clauses.append("c.chain_id = ?")
                params.append(chain_id)
            
            if sequence_id is not None:
                where_clauses.append("c.sequence_id = ?")
                params.append(sequence_id)
            
            if node_id is not None:
                where_clauses.append("c.node_id = ?")
                params.append(node_id)
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            
            query += " ORDER BY c.timestamp DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            result = []
            for row in rows:
                item = {
                    'id': row[0],
                    'content': row[1],
                    'content_type': row[2],
                    'source': row[3],
                    'timestamp': row[4],
                    'metadata': json.loads(row[5]) if row[5] else None,
                    'chain_id': row[6],
                    'sequence_id': row[7],
                    'node_id': row[8],
                    'image_id': row[9],
                    'image': {
                        'data': row[10],
                        'path': row[11],
                        'type': row[12],
                        'metadata': json.loads(row[13]) if row[13] else None
                    } if row[10] or row[11] else None
                }
                result.append(item)
            
            return result
        except sqlite3.Error as e:
            logger.error(f"Error getting context with images: {e}")
            return []
    
    def clear_context(self, chain_id: Optional[str] = None, sequence_id: Optional[str] = None,
                     older_than: Optional[float] = None):
        """Clear context data with optional filtering
        
        Args:
            chain_id: Clear only context for this chain ID
            sequence_id: Clear only context for this sequence ID
            older_than: Clear only context older than this timestamp
        """
        try:
            self._ensure_connection()
            cursor = self.conn.cursor()
            
            # First get image IDs to delete
            query = "SELECT image_id FROM context_items"
            params = []
            where_clauses = []
            
            if chain_id is not None:
                where_clauses.append("chain_id = ?")
                params.append(chain_id)
            
            if sequence_id is not None:
                where_clauses.append("sequence_id = ?")
                params.append(sequence_id)
            
            if older_than is not None:
                where_clauses.append("timestamp < ?")
                params.append(older_than)
            
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            
            cursor.execute(query, params)
            image_ids = [row[0] for row in cursor.fetchall() if row[0] is not None]
            
            # Delete context items
            delete_query = "DELETE FROM context_items"
            if where_clauses:
                delete_query += " WHERE " + " AND ".join(where_clauses)
            
            cursor.execute(delete_query, params)
            
            # Delete associated images
            if image_ids:
                placeholders = ",".join(["?" for _ in image_ids])
                cursor.execute(f"DELETE FROM context_images WHERE id IN ({placeholders})", image_ids)
            
            self.conn.commit()
            logger.info(f"Cleared context data: {cursor.rowcount} items removed")
        except sqlite3.Error as e:
            logger.error(f"Error clearing context: {e}")
    
    def close(self):
        """Close the database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def __del__(self):
        """Destructor to ensure database connection is closed"""
        self.close()