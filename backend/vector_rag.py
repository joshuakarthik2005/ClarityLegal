
"""
vector_rag.py Ã¢â‚¬â€ Supabase pgvector RAG module for ClarityLegal

Provides:
  - store_document_chunks(user_id, doc_name, text, doc_url)
      Extracts text Ã¢â€ â€™ splits into chunks Ã¢â€ â€™ stores in Supabase with full-text index
  - search_snippets(query, user_id, document_name, scope, limit)
      Searches stored chunks using PostgreSQL full-text search (ts_rank)
  - delete_document_chunks(user_id, doc_name)
      Removes all chunks for a given document

Uses PostgreSQL full-text search (tsvector/tsquery) for ranking.
pgvector column is reserved for future semantic search upgrade.
"""

import os
import re
import logging
from typing import List, Dict, Any, Optional

import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

_pool = None


def _get_connection():
    """Get a database connection (simple single-connection approach)."""
    global _pool
    db_url = os.getenv("SUPABASE_DB_URL", "")
    if not db_url:
        raise RuntimeError("SUPABASE_DB_URL not set in environment")
    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to Supabase: {e}")
        raise


def is_available() -> bool:
    """Check if the Supabase pgvector RAG is available."""
    db_url = os.getenv("SUPABASE_DB_URL", "")
    if not db_url:
        return False
    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM document_chunks LIMIT 0")
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"Supabase RAG not available: {e}")
        return False


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------

def _chunk_text(text: str, max_chunk_size: int = 400, overlap: int = 50) -> List[str]:
    """Split text into overlapping chunks at sentence boundaries."""
    if not text or not text.strip():
        return []

    # Split into sentences
    sentences = re.split(r'(?<=[.!?;])\s+', text.strip())

    chunks = []
    current_chunk = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # If adding this sentence would exceed the limit, save current chunk
        if current_chunk and len(current_chunk) + len(sentence) + 1 > max_chunk_size:
            if len(current_chunk) > 30:  # Skip tiny chunks
                chunks.append(current_chunk.strip())
            # Start new chunk with overlap from end of previous
            overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else ""
            current_chunk = overlap_text + " " + sentence
        else:
            current_chunk = (current_chunk + " " + sentence).strip() if current_chunk else sentence

    # Don't forget the last chunk
    if current_chunk and len(current_chunk.strip()) > 30:
        chunks.append(current_chunk.strip())

    return chunks


# ---------------------------------------------------------------------------
# Store document chunks
# ---------------------------------------------------------------------------

def store_document_chunks(
    user_id: str,
    document_name: str,
    full_text: str,
    document_url: str = ""
) -> Dict[str, Any]:
    """
    Split document text into chunks and store in Supabase.
    
    Returns dict with success status and chunk count.
    """
    if not full_text or not full_text.strip():
        return {"success": False, "error": "No text provided", "chunks_stored": 0}

    try:
        chunks = _chunk_text(full_text)
        if not chunks:
            return {"success": False, "error": "No valid chunks extracted", "chunks_stored": 0}

        conn = _get_connection()
        cur = conn.cursor()

        # Delete existing chunks for this document (re-upload support)
        cur.execute(
            "DELETE FROM document_chunks WHERE user_id = %s AND document_name = %s",
            (user_id, document_name)
        )

        # Insert new chunks
        values = [
            (user_id, document_name, document_url, chunk, idx)
            for idx, chunk in enumerate(chunks)
        ]

        execute_values(
            cur,
            """INSERT INTO document_chunks 
               (user_id, document_name, document_url, chunk_text, chunk_index) 
               VALUES %s""",
            values
        )

        conn.commit()
        conn.close()

        logger.info(f"Stored {len(chunks)} chunks for '{document_name}' (user: {user_id})")
        return {"success": True, "chunks_stored": len(chunks), "document_name": document_name}

    except Exception as e:
        logger.error(f"Error storing document chunks: {e}")
        return {"success": False, "error": str(e), "chunks_stored": 0}


# ---------------------------------------------------------------------------
# Search snippets using PostgreSQL full-text search
# ---------------------------------------------------------------------------

def search_snippets(
    query: str,
    user_id: Optional[str] = None,
    document_name: Optional[str] = None,
    scope: str = "user",
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Search document chunks using PostgreSQL full-text search with ts_rank.
    
    Args:
        query: Search query text
        user_id: Filter to this user's documents (optional)
        document_name: Filter to specific document (optional)  
        scope: "user" (all user docs) or "document" (specific doc)
        limit: Max results to return
    
    Returns:
        List of snippet dicts: [{text, source, relevance_score, document_url}]
    """
    if not query or not query.strip():
        return []

    try:
        conn = _get_connection()
        cur = conn.cursor()

        # Build the search query using PostgreSQL full-text search
        # plainto_tsquery handles multi-word queries automatically
        # We use ts_rank for relevance scoring
        
        # Also add a LIKE-based fallback for partial matches
        query_clean = query.strip()
        
        # Build WHERE clauses
        conditions = []
        params = []

        # Full-text search OR keyword ILIKE match
        conditions.append(
            "(to_tsvector('english', chunk_text) @@ plainto_tsquery('english', %s) "
            "OR chunk_text ILIKE %s)"
        )
        params.extend([query_clean, f"%{query_clean}%"])

        # User filter
        if user_id and scope == "user":
            conditions.append("user_id = %s")
            params.append(user_id)

        # Document filter
        if document_name and scope == "document":
            conditions.append("document_name = %s")
            params.append(document_name)

        where_clause = " AND ".join(conditions)
        params.append(limit)

        sql = f"""
            SELECT 
                chunk_text,
                document_name,
                document_url,
                ts_rank(
                    to_tsvector('english', chunk_text), 
                    plainto_tsquery('english', %s)
                ) as rank,
                CASE 
                    WHEN chunk_text ILIKE %s THEN 0.3 
                    ELSE 0.0 
                END as exact_bonus
            FROM document_chunks
            WHERE {where_clause}
            ORDER BY (rank + CASE WHEN chunk_text ILIKE %s THEN 0.3 ELSE 0.0 END) DESC
            LIMIT %s
        """

        # Add the ranking params at the beginning
        full_params = [query_clean, f"%{query_clean}%"] + params + [f"%{query_clean}%"]

        cur.execute(sql, full_params)
        rows = cur.fetchall()
        conn.close()

        results = []
        for row in rows:
            chunk_text, doc_name, doc_url, rank, exact_bonus = row
            # Normalize score to 0-1 range
            score = min(float(rank) + float(exact_bonus) + 0.1, 1.0)
            results.append({
                "text": chunk_text[:500],
                "source": doc_name,
                "relevance_score": round(score, 3),
                "document_url": doc_url or ""
            })

        logger.info(f"pgvector RAG: Found {len(results)} snippets for query: '{query}'")
        return results

    except Exception as e:
        logger.error(f"Error searching snippets: {e}")
        return []


# ---------------------------------------------------------------------------
# Delete document chunks
# ---------------------------------------------------------------------------

def delete_document_chunks(user_id: str, document_name: str) -> bool:
    """Remove all chunks for a given document."""
    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM document_chunks WHERE user_id = %s AND document_name = %s",
            (user_id, document_name)
        )
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        logger.info(f"Deleted {deleted} chunks for '{document_name}' (user: {user_id})")
        return True
    except Exception as e:
        logger.error(f"Error deleting chunks: {e}")
        return False


# ---------------------------------------------------------------------------
# Get chunk stats
# ---------------------------------------------------------------------------

def get_stats(user_id: Optional[str] = None) -> Dict[str, Any]:
    """Get statistics about stored document chunks."""
    try:
        conn = _get_connection()
        cur = conn.cursor()

        if user_id:
            cur.execute(
                "SELECT COUNT(*), COUNT(DISTINCT document_name) FROM document_chunks WHERE user_id = %s",
                (user_id,)
            )
        else:
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT document_name) FROM document_chunks")

        row = cur.fetchone()
        conn.close()

        return {
            "total_chunks": row[0],
            "total_documents": row[1],
            "available": True
        }
    except Exception as e:
        return {"total_chunks": 0, "total_documents": 0, "available": False, "error": str(e)}
