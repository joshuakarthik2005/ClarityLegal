-- Enable the pgvector extension 
CREATE EXTENSION IF NOT EXISTS vector;

-- Create the document chunks table (if not exists)
CREATE TABLE IF NOT EXISTS document_chunks (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    document_name TEXT NOT NULL,
    document_url TEXT DEFAULT '',
    chunk_text TEXT NOT NULL,
    chunk_index INTEGER DEFAULT 0,
    embedding vector(768),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Search function for the REST API
CREATE OR REPLACE FUNCTION match_documents (
  query_embedding vector(768),
  match_threshold float,
  match_count int,
  p_user_id text,
  p_document_name text
)
RETURNS TABLE (
  id integer,
  chunk_text text,
  document_name text,
  document_url text,
  similarity float
)
LANGUAGE sql STABLE
AS utf8
  SELECT
    dc.id,
    dc.chunk_text,
    dc.document_name,
    dc.document_url,
    1 - (dc.embedding <=> query_embedding) AS similarity
  FROM document_chunks dc
  WHERE 1 - (dc.embedding <=> query_embedding) > match_threshold
    AND (p_user_id IS NULL OR dc.user_id = p_user_id)
    AND (p_document_name IS NULL OR dc.document_name = p_document_name)
  ORDER BY dc.embedding <=> query_embedding
  LIMIT match_count;
utf8;

-- Create an HNSW index for fast similarity search
CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx ON document_chunks USING hnsw (embedding vector_cosine_ops);
