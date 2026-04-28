"""One-time script to set up Supabase pgvector schema."""
import psycopg2

DB_URL = "postgresql://postgres:6ju1bmsUw1jj1pGl@db.eouhcczwrlpdafnczsul.supabase.co:5432/postgres"

conn = psycopg2.connect(DB_URL)
conn.autocommit = True
cur = conn.cursor()

# Enable pgvector
cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
print("pgvector extension enabled")

# Create document_chunks table
cur.execute("""
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
""")
print("Table 'document_chunks' created")

# Create indexes
cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_user ON document_chunks (user_id);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON document_chunks (document_name);")

# Create full-text search index
cur.execute("""
CREATE INDEX IF NOT EXISTS idx_chunks_fts 
ON document_chunks 
USING GIN (to_tsvector('english', chunk_text));
""")
print("Indexes created")

# Verify
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'document_chunks' ORDER BY ordinal_position;")
for row in cur.fetchall():
    print(f"  Column: {row[0]:20s} Type: {row[1]}")

conn.close()
print("\nSchema setup complete!")
