"""
Semantic Document Comparison Router
Advanced AI-powered document comparison using Google Cloud Vertex AI.
"""

import re
import json
import asyncio
from typing import List, Dict, Any, Optional
from io import BytesIO

import fitz  # PyMuPDF
import numpy as np
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse
from sklearn.metrics.pairwise import cosine_similarity
from google.cloud import aiplatform
import os
from groq import Groq

from auth import get_current_user

# Initialize router
router = APIRouter(prefix="/api/compare", tags=["Document Comparison"])

# Initialize AI models
_embedding_model = None
_groq_client = None

def get_groq_client():
    """Get or initialize the Groq client."""
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if api_key:
            _groq_client = Groq(api_key=api_key)
    return _groq_client

def get_generative_model():
    """Get Groq client for text generation (replaces Vertex AI GenerativeModel)."""
    client = get_groq_client()
    if not client:
        raise HTTPException(status_code=503, detail="AI model not configured")
    return client

def extract_text_from_pdf(file: UploadFile) -> str:
    """
    Extract all text content from a PDF file using PyMuPDF.
    
    Args:
        file: The uploaded PDF file
        
    Returns:
        Extracted text as a string
        
    Raises:
        HTTPException: If PDF processing fails
    """
    try:
        # Read file content
        file_content = file.file.read()
        
        # Open PDF with PyMuPDF
        pdf_document = fitz.open(stream=file_content, filetype="pdf")
        
        text_content = ""
        for page_num in range(pdf_document.page_count):
            page = pdf_document.load_page(page_num)
            text_content += page.get_text()
            text_content += "\n\n"  # Add page separator
        
        pdf_document.close()
        
        # Reset file pointer for potential reuse
        file.file.seek(0)
        
        return text_content.strip()
        
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to extract text from PDF: {str(e)}"
        )

def segment_into_clauses(text: str) -> List[str]:
    """
    Segment document text into meaningful clauses based on paragraph breaks.
    
    Args:
        text: The full document text
        
    Returns:
        List of clause strings
    """
    # Split by two or more consecutive newlines
    clauses = re.split(r'\n\s*\n+', text)
    
    # Clean up clauses: remove empty ones and strip whitespace
    cleaned_clauses = []
    for clause in clauses:
        cleaned_clause = clause.strip()
        if cleaned_clause and len(cleaned_clause) > 10:  # Filter out very short segments
            cleaned_clauses.append(cleaned_clause)
    
    return cleaned_clauses

async def get_embeddings(clauses: List[str]) -> List[List[float]]:
    """
    Generate vector embeddings for a list of text clauses using Vertex AI.
    
    Args:
        clauses: List of text clauses
        
    Returns:
        List of embedding vectors
        
    Raises:
        HTTPException: If embedding generation fails
    """
    try:
        model = get_embedding_model()
        
        # Process in batches to avoid API limits
        batch_size = 5
        all_embeddings = []
        
        for i in range(0, len(clauses), batch_size):
            batch = clauses[i:i + batch_size]
            embeddings = model.get_embeddings(batch)
            
            # Extract the vector values
            batch_vectors = [embedding.values for embedding in embeddings]
            all_embeddings.extend(batch_vectors)
            
            # Small delay to respect API rate limits
            await asyncio.sleep(0.1)
        
        return all_embeddings
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate embeddings: {str(e)}"
        )

def find_semantic_matches(
    original_embeddings: List[List[float]], 
    revised_embeddings: List[List[float]], 
    original_clauses: List[str], 
    revised_clauses: List[str],
    threshold: float = 0.75
) -> Dict[str, Any]:
    """
    Find semantic matches between original and revised document clauses.
    
    Args:
        original_embeddings: Embeddings for original document clauses
        revised_embeddings: Embeddings for revised document clauses
        original_clauses: Text of original document clauses
        revised_clauses: Text of revised document clauses
        threshold: Similarity threshold for matching
        
    Returns:
        Dictionary containing matched, added, and deleted clauses
    """
    # Convert to numpy arrays for efficient computation
    original_matrix = np.array(original_embeddings)
    revised_matrix = np.array(revised_embeddings)
    
    # Compute cosine similarity matrix
    similarity_matrix = cosine_similarity(revised_matrix, original_matrix)
    
    matched_pairs = []
    added_clauses = []
    matched_original_indices = set()
    
    # Process each revised clause
    for revised_idx, revised_clause in enumerate(revised_clauses):
        # Find best match in original document
        similarities = similarity_matrix[revised_idx]
        best_original_idx = np.argmax(similarities)
        best_similarity = similarities[best_original_idx]
        
        if best_similarity >= threshold:
            # Found a match
            matched_pairs.append({
                'original_text': original_clauses[best_original_idx],
                'revised_text': revised_clause,
                'similarity': float(best_similarity),
                'original_index': best_original_idx,
                'revised_index': revised_idx
            })
            matched_original_indices.add(best_original_idx)
        else:
            # No good match found - this is an added clause
            added_clauses.append(revised_clause)
    
    # Find deleted clauses (original clauses that weren't matched)
    deleted_clauses = [
        original_clauses[i] for i in range(len(original_clauses))
        if i not in matched_original_indices
    ]
    
    return {
        'matched_pairs': matched_pairs,
        'added_clauses': added_clauses,
        'deleted_clauses': deleted_clauses
    }

async def analyze_semantic_diff(original_text: str, revised_text: str) -> Dict[str, str]:
    """
    Use Gemini Pro to analyze the semantic difference between two clauses.
    
    Args:
        original_text: Original clause text
        revised_text: Revised clause text
        
    Returns:
        Dictionary with analysis results
    """
    prompt = f"""You are a meticulous paralegal specializing in contract analysis. Compare the following two versions of a legal clause. First, summarize the change in one sentence. Second, explain the practical implication of this change for the user. Finally, classify the change as 'Beneficial', 'Harmful', or 'Neutral' for the user. Structure your response as a JSON object with keys: "summary", "implication", and "classification".

**Original Clause:**
\"\"\"
{original_text}
\"\"\"

**Revised Clause:**
\"\"\"
{revised_text}
\"\"\""""

    try:
        client = get_generative_model()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.3,
        )
        response_text = response.choices[0].message.content.strip()
        
        # Find JSON-like content within the response
        try:
            if '{' in response_text and '}' in response_text:
                start_idx = response_text.find('{')
                end_idx = response_text.rfind('}') + 1
                json_text = response_text[start_idx:end_idx]
                return json.loads(json_text)
            else:
                return {
                    "summary": "Changes detected between clauses",
                    "implication": response_text,
                    "classification": "Neutral"
                }
        except json.JSONDecodeError:
            return {
                "summary": "Changes detected between clauses",
                "implication": response_text,
                "classification": "Neutral"
            }
    
    except Exception as e:
        # Return error information
        return {
            "summary": "Analysis failed",
            "implication": f"Could not analyze changes: {str(e)}",
            "classification": "Neutral"
        }

@router.post("/compare-documents")
async def compare_documents(
    original_file: UploadFile = File(..., description="Original document PDF"),
    revised_file: UploadFile = File(..., description="Revised document PDF"),
    current_user: dict = Depends(get_current_user)
):
    """
    Compare two PDF documents and identify semantic differences using AI.
    
    This endpoint processes two PDF documents, extracts their text content,
    segments them into clauses, and uses AI to identify meaningful differences.
    
    Returns:
        JSON object containing added, deleted, and changed clauses with AI analysis
    """
    
    # Validate file types
    if not original_file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Original file must be a PDF")
    if not revised_file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Revised file must be a PDF")
    
    try:
        # Step 1: Extract text from both PDFs
        original_text = extract_text_from_pdf(original_file)
        revised_text = extract_text_from_pdf(revised_file)
        
        # Step 2: Segment documents into clauses
        original_clauses = segment_into_clauses(original_text)
        revised_clauses = segment_into_clauses(revised_text)
        
        if not original_clauses:
            raise HTTPException(status_code=400, detail="No readable content found in original document")
        if not revised_clauses:
            raise HTTPException(status_code=400, detail="No readable content found in revised document")
        
        # Step 3: Generate embeddings
        original_embeddings = await get_embeddings(original_clauses)
        revised_embeddings = await get_embeddings(revised_clauses)
        
        # Step 4: Find semantic matches
        match_results = find_semantic_matches(
            original_embeddings, revised_embeddings,
            original_clauses, revised_clauses
        )
        
        # Step 5: Analyze changes with AI
        changed_clauses = []
        analysis_tasks = []
        
        for pair in match_results['matched_pairs']:
            # Only analyze if the text is actually different
            if pair['original_text'].strip() != pair['revised_text'].strip():
                analysis_tasks.append(
                    analyze_semantic_diff(pair['original_text'], pair['revised_text'])
                )
                changed_clauses.append(pair)
        
        # Execute AI analysis in parallel
        if analysis_tasks:
            ai_analyses = await asyncio.gather(*analysis_tasks, return_exceptions=True)
            
            # Combine results
            for i, analysis in enumerate(ai_analyses):
                if isinstance(analysis, dict):
                    changed_clauses[i]['ai_analysis'] = analysis
                else:
                    # Handle exceptions
                    changed_clauses[i]['ai_analysis'] = {
                        "summary": "Analysis failed",
                        "implication": "Could not analyze this change",
                        "classification": "Neutral"
                    }
        
        # Step 6: Prepare final response
        result = {
            "addedClauses": match_results['added_clauses'],
            "deletedClauses": match_results['deleted_clauses'],
            "changedClauses": [
                {
                    "originalText": clause['original_text'],
                    "revisedText": clause['revised_text'],
                    "aiAnalysis": clause.get('ai_analysis', {
                        "summary": "No changes detected",
                        "implication": "Text appears identical",
                        "classification": "Neutral"
                    })
                }
                for clause in changed_clauses
            ],
            "summary": {
                "totalChanges": len(changed_clauses),
                "additions": len(match_results['added_clauses']),
                "deletions": len(match_results['deleted_clauses']),
                "originalClauses": len(original_clauses),
                "revisedClauses": len(revised_clauses)
            }
        }
        
        return JSONResponse(content=result)
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Handle unexpected errors
        raise HTTPException(
            status_code=500,
            detail=f"Document comparison failed: {str(e)}"
        )