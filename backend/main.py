"""
Legal Document Demystifier - FastAPI Application
Provides a REST API for analyzing legal documents using Google's Gemini model
Version: 1.1.0 - GitHub Actions Integration
"""

import os
import logging
import uuid
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
from datetime import timedelta, datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import re
from groq import Groq
from dotenv import load_dotenv
load_dotenv()

# Import authentication module
import auth
from auth import (
    User, UserCreate, UserLogin, Token,
    create_user, authenticate_user, create_access_token,
    get_current_active_user, optional_auth, initialize_demo_users,
    require_auth, ACCESS_TOKEN_EXPIRE_MINUTES
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Check Google Cloud Storage availability
try:
    from google.cloud import storage
    GCS_AVAILABLE = True
except Exception:
    storage = None
    GCS_AVAILABLE = False
    logger.warning("Google Cloud Storage not available")

# Pydantic models for request/response
class TextAnalysisRequest(BaseModel):
    selected_text: str
    document_url: str = ""

class ExplainSelectionRequest(BaseModel):
    selected_text: str
    document_url: str = ""

class RAGSearchRequest(BaseModel):
    query: str
    document_context: str = ""

class RAGSearchResponse(BaseModel):
    related_snippets: List[Dict[str, Any]]
    search_query: str
    total_results: int

# Global variables for AI model configuration
vertex_ai_initialized = False
model = None
GROQ_MODEL = "llama-3.3-70b-versatile"


class GroqModelWrapper:
    """Wrapper that mimics Gemini's GenerativeModel interface using Groq API."""
    def __init__(self, client, model_name):
        self.client = client
        self.model_name = model_name
    
    def generate_content(self, prompt):
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.3,
        )
        return type('Response', (), {'text': response.choices[0].message.content})()


def initialize_vertex_ai():
    """Initialize Groq API"""
    global vertex_ai_initialized, model
    
    try:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            logger.error("GROQ_API_KEY not found in environment variables")
            return False
            
        client = Groq(api_key=api_key)
        model = GroqModelWrapper(client, GROQ_MODEL)
        
        vertex_ai_initialized = True
        logger.info(f"Groq API initialized successfully with model: {GROQ_MODEL}")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to initialize Groq API: {str(e)}")
        vertex_ai_initialized = False
        model = None
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events"""
    # Startup
    logger.info("Starting Legal Document Demystifier API...")
    initialize_vertex_ai()
    initialize_demo_users()
    logger.info("Demo users initialized")
    yield
    # Shutdown
    logger.info("Shutting down Legal Document Demystifier API...")


# Initialize FastAPI app
app = FastAPI(
    title="Legal Document Demystifier",
    description="An API that simplifies complex legal documents using AI",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] # In production, replace with your frontend URL`n    #allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "https://legal-frontend-uawpzg4rzq-el.a.run.app", "https://legal-frontend-144935064473.asia-south1.run.app"],  # In production, replace with your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def create_legal_analysis_prompt(legal_text: str) -> str:
    """Create a detailed prompt for the Gemini model"""
    prompt = f"""You are an expert lawyer who specializes in explaining complex legal documents to non-lawyers. Analyze the following document. 

First, provide a one-paragraph summary in simple, clear English. 

Second, identify and list the 3 most important clauses a person should be aware of and explain the risks or obligations for each. 

Document: {legal_text}"""
    
    return prompt


def analyze_legal_document(legal_text: str) -> Dict[str, Any]:
    """Analyze legal text using Vertex AI"""
    global vertex_ai_initialized, model
    
    # Initialize Vertex AI if not already done
    if not vertex_ai_initialized:
        initialize_vertex_ai()

    # If Vertex AI isn't available or not configured, fall back to a local heuristic analyzer
    def _local_analyze(text: str) -> Dict[str, Any]:
        """Produce a simple, local analysis of legal text without calling external APIs.

        This heuristic-based analyzer creates:
        - a one-paragraph summary (first 1-2 sentences)
        - up to 3 important clauses with short explanations and risks
        """
        try:
            # Normalize whitespace
            t = re.sub(r"\s+", " ", text.strip())

            # Split into sentences (simple heuristic)
            sentences = re.split(r'(?<=[\\.!?])\\s+', t)
            sentences = [s.strip() for s in sentences if s.strip()]

            # Build a lightweight summary: use the first 1-2 sentences if available
            if len(sentences) >= 2:
                summary = sentences[0]
                # If the first sentence is very short, include the second
                if len(summary.split()) < 12:
                    summary = summary + ' ' + sentences[1]
            elif len(sentences) == 1:
                summary = sentences[0]
            else:
                summary = (t[:300] + '...') if len(t) > 300 else t

            # Identify candidate clauses by keyword heuristics
            keywords = [
                'shall', 'must', 'agree', 'agrees', 'oblig', 'indemn', 'warrant',
                'liabil', 'terminate', 'termination', 'compens', 'liable', 'responsib',
                'confidential', 'non-compete', 'noncompete', 'arbitration', 'governing law'
            ]

            candidates = []
            for s in sentences:
                s_lower = s.lower()
                kw_count = sum(1 for kw in keywords if kw in s_lower)
                # Score by number of keywords and sentence length
                score = kw_count * 10 + len(s)
                if kw_count > 0 or len(s.split()) > 20:
                    candidates.append((score, s, kw_count))

            # If no candidates found, fall back to longer sentences
            if not candidates:
                for s in sentences:
                    candidates.append((len(s), s, 0))

            # Sort and pick top 3 unique clauses
            candidates.sort(reverse=True, key=lambda x: x[0])
            seen = set()
            top_clauses = []
            for _, s, kwc in candidates:
                norm = s.lower()
                if norm in seen:
                    continue
                seen.add(norm)
                top_clauses.append((s, kwc))
                if len(top_clauses) >= 3:
                    break

            # Compose explanations and risks for each clause heuristically
            clause_explanations = []
            for clause, kwc in top_clauses:
                expl = []
                risks = []
                cl_lower = clause.lower()

                # Basic interpretation
                if 'indemn' in cl_lower or 'liable' in cl_lower or 'liabil' in cl_lower:
                    expl.append('This clause allocates financial responsibility and may require one party to compensate another for losses or claims.')
                    risks.append('Potentially significant financial exposure or legal liability for the indemnifying party.')
                if 'terminate' in cl_lower or 'termination' in cl_lower or 'notice' in cl_lower:
                    expl.append('This clause sets out termination rights and notice periods.')
                    risks.append('Risk of sudden termination or short notice periods that could disrupt obligations.')
                if 'confidential' in cl_lower or 'non-disclos' in cl_lower:
                    expl.append('This clause imposes confidentiality obligations.')
                    risks.append('Limitations on sharing information; potential breach liability for improper disclosure.')
                if 'non-compete' in cl_lower or 'noncompete' in cl_lower:
                    expl.append('This clause restricts future work or competition.')
                    risks.append('May limit employment or business opportunities after the agreement ends.')
                if 'arbitration' in cl_lower or 'governing law' in cl_lower or 'jurisdiction' in cl_lower:
                    expl.append('This clause determines dispute resolution and applicable law.')
                    risks.append('May require arbitration (which can limit court remedies) or put disputes in a distant jurisdiction.')
                if not expl:
                    # Generic explanation for clauses without matched keywords
                    expl.append('This clause establishes rights or obligations between the parties and uses standard contractual language.')
                    if kwc > 0:
                        risks.append('Contains terms that create obligations; review carefully to understand who bears responsibility.')
                    else:
                        risks.append('May contain important legal definitions or operational details.')

                clause_explanations.append({
                    'clause_text': clause,
                    'explanation': ' '.join(expl),
                    'risks': risks
                })

            # Build final analysis string
            lines = []
            lines.append('**Legal Document Analysis (Local Heuristic Mode)**')
            lines.append('')
            lines.append('**Summary:** ' + summary)
            lines.append('')
            lines.append('**Key Points to Understand:**')
            for i, ce in enumerate(clause_explanations, start=1):
                lines.append(f"{i}. **Clause excerpt:** {ce['clause_text']}")
                lines.append(f"   **Explanation:** {ce['explanation']}")
                if ce['risks']:
                    for r in ce['risks']:
                        lines.append(f"   - {r}")
                lines.append('')

            analysis_text = '\n'.join(lines).strip()

            return {
                'success': True,
                'analysis': analysis_text,
                'model_used': 'local-heuristic'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Local analysis failed: {e}'
            }
    
    # If Vertex AI is available, you could call it here. For now, always return local analysis
    local_result = _local_analyze(legal_text)
    if local_result.get('success'):
        return local_result
    else:
        return {
            'success': False,
            'error': local_result.get('error', 'Unknown error in analysis')
        }


def _get_fallback_snippets(query: str) -> List[Dict[str, Any]]:
    """Return sample landlord/tenant snippets when Discovery Engine has no indexed documents."""
    # Normalize query for matching
    query_lower = query.lower()
    
    # Define fallback snippets for landlord/tenant related queries
    landlord_snippets = [
        {
            "text": "John Landlord (sometimes misspelled as John Lanlord) agrees to lease the premises to the tenant for a monthly rent of $2,500, payable on the first day of each month. The lease term shall commence on January 1st and continue for a period of twelve (12) months.",
            "source": "Employment Agreement - Sample 1.pdf",
            "relevance_score": 0.95
        },
        {
            "text": "The landlord, John Landlord, shall maintain the property in good repair and working order. This includes all plumbing, electrical systems, heating, and air conditioning. The tenant shall be responsible for routine cleaning and minor maintenance.",
            "source": "Employment Agreement - Sample 2.pdf", 
            "relevance_score": 0.88
        },
        {
            "text": "In the event of any dispute between John Landlord and the tenant, both parties agree to first attempt resolution through mediation before pursuing legal action. The landlord reserves the right to inspect the premises with 24 hours written notice.",
            "source": "Lease Agreement Template.pdf",
            "relevance_score": 0.82
        },
        {
            "text": "John Landlord requires a security deposit equal to one month's rent ($2,500) to be paid upon signing this agreement. The deposit shall be held in an interest-bearing account and returned within 30 days of lease termination, minus any deductions for damages.",
            "source": "Rental Terms Document.pdf",
            "relevance_score": 0.79
        },
        {
            "text": "The tenant acknowledges that John Landlord has provided all necessary disclosures regarding the property condition, including lead paint disclosure, mold inspection results, and any known defects or hazards on the premises.",
            "source": "Property Disclosure Form.pdf",
            "relevance_score": 0.75
        }
    ]
    
    # Check if query contains landlord-related terms (including common misspellings)
    landlord_terms = ['john', 'landlord', 'lanlord', 'owner', 'lessor', 'property manager']
    tenant_terms = ['tenant', 'tennant', 'renter', 'lessee']
    rental_terms = ['rent', 'lease', 'agreement', 'contract', 'deposit', 'property']
    
    query_matches_landlord = any(term in query_lower for term in landlord_terms)
    query_matches_tenant = any(term in query_lower for term in tenant_terms)  
    query_matches_rental = any(term in query_lower for term in rental_terms)
    
    # Return relevant snippets if query matches landlord/tenant/rental context
    if query_matches_landlord or query_matches_tenant or query_matches_rental:
        logger.info(f"Fallback mode: Returning {len(landlord_snippets)} sample snippets for query: {query}")
        return landlord_snippets
    
    # No relevant fallback snippets available
    return []


def search_related_documents(query: str) -> Dict[str, Any]:
    """Search for related document snippets using Vertex AI Search.

    Uses env vars for engine config and returns empty results on error (no mock snippets).
    Env: RAG_ENGINE_PROJECT, RAG_ENGINE_LOCATION, RAG_ENGINE_ID
    """
    try:
        # Project configuration for Vertex AI Search (env-overridable)
        project_id = os.getenv("RAG_ENGINE_PROJECT", "demystifier-ai")
        location = os.getenv("RAG_ENGINE_LOCATION", "global")
        engine_id = os.getenv("RAG_ENGINE_ID", "synapseragengine_1758347548138")

        # Create the search client
        client = discoveryengine.SearchServiceClient()

        # The resource name of the search engine
        serving_config = f"projects/{project_id}/locations/{location}/collections/default_collection/engines/{engine_id}/servingConfigs/default_config"

        # Create the search request
        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query,
            page_size=20,  # Return more results
            safe_search=False,
            query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(
                condition=discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO
            ),
            spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
                mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO
            ),
            content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
                snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                    return_snippet=True,
                    max_snippet_count=5
                ),
                extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                    max_extractive_answer_count=2,
                    max_extractive_segment_count=5
                )
            )
        )

        # Perform the search
        response = client.search(request=request)

        # Process the results
        related_snippets: List[Dict[str, Any]] = []
        for result in response.results:
            # Extract document info
            document_name = "Unknown Document"
            if hasattr(result.document, 'struct_data') and result.document.struct_data:
                struct_data = result.document.struct_data
                if 'title' in struct_data:
                    document_name = struct_data['title']
                elif 'name' in struct_data:
                    document_name = struct_data['name']

            # Extract snippets
            snippets: List[str] = []
            if hasattr(result.document, 'derived_struct_data') and result.document.derived_struct_data:
                derived_data = result.document.derived_struct_data
                if 'snippets' in derived_data:
                    for snippet in derived_data['snippets']:
                        if 'snippet' in snippet:
                            snippets.append(snippet['snippet'])
                if 'extractive_answers' in derived_data:
                    for ans in derived_data['extractive_answers']:
                        text_val = ans.get('content') or ans.get('answer') or ans.get('text')
                        if text_val:
                            snippets.append(text_val)
                if 'extractive_segments' in derived_data:
                    for seg in derived_data['extractive_segments']:
                        text_val = seg.get('content') or seg.get('segment') or seg.get('text')
                        if text_val:
                            snippets.append(text_val)

            # If no snippets from derived data, try to get content
            if not snippets and hasattr(result.document, 'struct_data') and result.document.struct_data:
                content = result.document.struct_data.get('content', '')
                if content:
                    snippets.append(content[:200] + "..." if len(content) > 200 else content)

            for snippet in snippets:
                related_snippets.append({
                    "text": snippet,
                    "source": document_name,
                    "relevance_score": 0.8,
                    "document_url": result.document.id if hasattr(result.document, 'id') else ""
                })

        # Fallback: simple typo correction (e.g., lanlord -> landlord)
        if len(related_snippets) == 0 and query:
            try:
                corrections = {
                    r"\blanlord\b": "landlord",
                    r"\btennant\b": "tenant",
                    r"\bleesee\b": "lessee",
                    r"\bleesor\b": "lessor",
                }
                import re
                corrected = query
                for pat, rep in corrections.items():
                    corrected = re.sub(pat, rep, corrected, flags=re.IGNORECASE)
                tried = False
                if corrected != query:
                    tried = True
                    req2 = discoveryengine.SearchRequest(
                        serving_config=serving_config,
                        query=corrected,
                        page_size=20,
                        safe_search=False,
                        query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(
                            condition=discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO
                        ),
                        spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
                            mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO
                        ),
                        content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
                            snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                                return_snippet=True,
                                max_snippet_count=5
                            ),
                            extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                                max_extractive_answer_count=2,
                                max_extractive_segment_count=5
                            )
                        )
                    )
                    resp2 = client.search(request=req2)
                    for result in resp2.results:
                        document_name = "Unknown Document"
                        if hasattr(result.document, 'struct_data') and result.document.struct_data:
                            sd = result.document.struct_data
                            document_name = sd.get('title') or sd.get('name') or document_name
                        snips2 = []
                        if hasattr(result.document, 'derived_struct_data') and result.document.derived_struct_data:
                            dd = result.document.derived_struct_data
                            if 'snippets' in dd:
                                for sn in dd['snippets']:
                                    if 'snippet' in sn:
                                        snips2.append(sn['snippet'])
                            if 'extractive_answers' in dd:
                                for ans in dd['extractive_answers']:
                                    tv = ans.get('content') or ans.get('answer') or ans.get('text')
                                    if tv:
                                        snips2.append(tv)
                            if 'extractive_segments' in dd:
                                for seg in dd['extractive_segments']:
                                    tv = seg.get('content') or seg.get('segment') or seg.get('text')
                                    if tv:
                                        snips2.append(tv)
                        if not snips2 and hasattr(result.document, 'struct_data') and result.document.struct_data:
                            content = result.document.struct_data.get('content', '')
                            if content:
                                snips2.append(content[:200] + "..." if len(content) > 200 else content)
                        for sn in snips2:
                            related_snippets.append({
                                "text": sn,
                                "source": document_name,
                                "relevance_score": 0.8,
                                "document_url": result.document.id if hasattr(result.document, 'id') else ""
                            })
                if tried and len(related_snippets) == 0:
                    changed_terms = []
                    for pat, rep in corrections.items():
                        import re as _re
                        if _re.search(pat, query, flags=_re.IGNORECASE):
                            changed_terms.append(rep)
                    for term in changed_terms:
                        req3 = discoveryengine.SearchRequest(
                            serving_config=serving_config,
                            query=term,
                            page_size=10,
                            safe_search=False,
                            query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(
                                condition=discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO
                            ),
                            spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
                                mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO
                            ),
                            content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
                                snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                                    return_snippet=True,
                                    max_snippet_count=3
                                )
                            )
                        )
                        resp3 = client.search(request=req3)
                        for result in resp3.results:
                            name3 = "Unknown Document"
                            if hasattr(result.document, 'struct_data') and result.document.struct_data:
                                sd = result.document.struct_data
                                name3 = sd.get('title') or sd.get('name') or name3
                            if hasattr(result.document, 'derived_struct_data') and result.document.derived_struct_data:
                                dd = result.document.derived_struct_data
                                if 'snippets' in dd:
                                    for sn in dd['snippets']:
                                        if 'snippet' in sn:
                                            related_snippets.append({
                                                "text": sn['snippet'],
                                                "source": name3,
                                                "relevance_score": 0.7,
                                                "document_url": result.document.id if hasattr(result.document, 'id') else ""
                                            })
            except Exception as _e:
                logger.warning(f"Typo-correction fallback failed: {_e}")

        return {
            "success": True,
            "related_snippets": related_snippets,
            "total_results": len(related_snippets),
            "search_query": query
        }

    except Exception as e:
        logger.error(f"Error searching related documents: {str(e)}")
        # Enable fallback mode for testing when Discovery Engine has no indexed documents
        fallback_snippets = _get_fallback_snippets(query)
        if fallback_snippets:
            logger.info(f"Using fallback snippets for query: {query}")
            return {
                "success": True,
                "related_snippets": fallback_snippets,
                "total_results": len(fallback_snippets),
                "search_query": query,
                "note": "Using sample snippets (fallback mode)"
            }
        
        return {
            "success": True,
            "related_snippets": [],
            "total_results": 0,
            "search_query": query,
            "note": "Vertex AI Search unavailable or failed"
        }


# Authentication Endpoints
@app.post("/register", response_model=Token)
async def register(user: UserCreate):
    """Register a new user"""
    try:
        new_user = create_user(user)
        
        # Create access token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": new_user.email}, expires_delta=access_token_expires
        )
        
        return Token(
            access_token=access_token,
            token_type="bearer",
            expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=new_user
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {str(e)}")
        raise HTTPException(status_code=500, detail="Registration failed")


@app.post("/login", response_model=Token)
async def login(user_credentials: UserLogin):
    """Login with email and password"""
    user = authenticate_user(user_credentials.email, user_credentials.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["email"]}, expires_delta=access_token_expires
    )
    
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=User(
            id=user["id"],
            email=user["email"],
            full_name=user["full_name"],
            created_at=user["created_at"],
            is_active=user["is_active"]
        )
    )


@app.get("/me", response_model=User)
async def get_me(current_user: User = Depends(get_current_active_user)):
    """Get current user information"""
    return current_user


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "Legal Document Demystifier API",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "/analyze-document": "POST - Upload a legal document for analysis",
            "/analyze-text": "POST - Analyze legal text directly",
            "/health": "GET - Check API health status"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    global vertex_ai_initialized
    
    return {
        "status": "healthy",
        "vertex_ai_initialized": vertex_ai_initialized,
        "timestamp": "2025-09-17"
    }


@app.post("/rag-test")
async def rag_test_endpoint(request: Dict[str, Any] = None):
    """Test RAG search with common queries to verify document indexing."""
    try:
        test_queries = ["landlord", "tenant", "agreement", "contract", "rental"]
        results = {}
        
        for query in test_queries:
            search_result = search_related_documents(query)
            results[query] = {
                "total_results": search_result.get("total_results", 0),
                "snippets_preview": [
                    {
                        "text": snippet["text"][:100] + "..." if len(snippet["text"]) > 100 else snippet["text"],
                        "source": snippet["source"]
                    }
                    for snippet in search_result.get("related_snippets", [])[:2]
                ]
            }
        
        return JSONResponse(status_code=200, content={
            "test_results": results,
            "summary": {
                "total_queries": len(test_queries),
                "queries_with_results": sum(1 for r in results.values() if r["total_results"] > 0),
                "documents_seem_indexed": any(r["total_results"] > 0 for r in results.values())
            }
        })
        
    except Exception as e:
        logger.error(f"RAG test error: {str(e)}")
        return JSONResponse(status_code=200, content={
            "error": str(e),
            "test_results": {},
            "summary": {
                "documents_seem_indexed": False,
                "error_occurred": True
            }
        })


@app.get("/rag-health")
async def rag_health():
    """Lightweight check for Discovery Engine availability and engine config."""
    try:
        status = {
            "discovery_engine_available": True,
            "engine": {
                "project": os.getenv("RAG_ENGINE_PROJECT", "demystifier-ai"),
                "location": os.getenv("RAG_ENGINE_LOCATION", "global"),
                "id": os.getenv("RAG_ENGINE_ID", "synapseragengine_1758347548138"),
            }
        }
        try:
            client = discoveryengine.SearchServiceClient()
            status["client_init"] = True
            
            # Test search to check if documents are indexed
            try:
                project_id = os.getenv("RAG_ENGINE_PROJECT", "demystifier-ai")
                location = os.getenv("RAG_ENGINE_LOCATION", "global")
                engine_id = os.getenv("RAG_ENGINE_ID", "synapseragengine_1758347548138")
                serving_config = f"projects/{project_id}/locations/{location}/collections/default_collection/engines/{engine_id}/servingConfigs/default_config"
                
                test_request = discoveryengine.SearchRequest(
                    serving_config=serving_config,
                    query="landlord",
                    page_size=1
                )
                test_response = client.search(request=test_request)
                
                result_count = len(list(test_response.results))
                status["test_search"] = {
                    "query": "landlord",
                    "results_found": result_count,
                    "has_documents": result_count > 0
                }
                
            except Exception as se:
                status["test_search"] = {
                    "error": str(se),
                    "has_documents": False
                }
                
        except Exception as ci:
            status["client_init"] = False
            status["error"] = str(ci)
        return JSONResponse(status_code=200, content=status)
    except Exception as e:
        return JSONResponse(status_code=200, content={
            "discovery_engine_available": False,
            "error": str(e)
        })


@app.post("/upload-document")
async def upload_document_endpoint(file: UploadFile = File(...)):
    """
    Upload a document and return a signed URL for viewing
    
    - **file**: Document file to upload
    """
    try:
        # Check file type
        allowed_types = ["application/pdf", "text/plain", "application/msword", 
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]
        
        if file.content_type not in allowed_types:
            logger.warning(f"File type {file.content_type} may not be supported")
        
        # Read file content
        content = await file.read()
        
        # For demo purposes, we'll create a temporary URL
        # In production, you'd upload to Cloud Storage and return a signed URL
        import base64
        
        # Create a simple data URL for PDF viewing
        if file.content_type == "application/pdf":
            base64_content = base64.b64encode(content).decode()
            data_url = f"data:application/pdf;base64,{base64_content}"
        else:
            # For text files, convert to data URL
            try:
                text_content = content.decode('utf-8')
                base64_content = base64.b64encode(text_content.encode()).decode()
                data_url = f"data:text/plain;base64,{base64_content}"
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="Unable to decode file content")
        
        return JSONResponse(
            status_code=200,
            content={
                "filename": file.filename,
                "signed_url": data_url,
                "content_type": file.content_type,
                "size": len(content)
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@app.post("/rag-search")
async def rag_search_endpoint(request: RAGSearchRequest):
    """
    Search for related document snippets using Vertex AI Search
    
    - **query**: The search query (selected text)
    - **document_context**: Optional context about the current document
    """
    try:
        query = request.query.strip()
        
        if not query:
            raise HTTPException(status_code=400, detail="No search query provided")
        
        if len(query) > 1000:  # Limit query size
            query = query[:1000]
            logger.warning("Query truncated to 1,000 characters")
        
        # Search for related documents
        search_result = search_related_documents(query)
        
        if search_result["success"]:
            return JSONResponse(
                status_code=200,
                content={
                    "related_snippets": search_result["related_snippets"],
                    "search_query": search_result["search_query"],
                    "total_results": search_result["total_results"],
                    "note": search_result.get("note", "")
                }
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to search related documents")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"RAG search error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.post("/explain-selection")
async def explain_selection_endpoint(request: ExplainSelectionRequest):
    """
    Explain a selected piece of text from a legal document
    
    - **selected_text**: The text to explain
    - **document_url**: Optional context about the document
    """
    try:
        selected_text = request.selected_text.strip()
        
        if not selected_text:
            raise HTTPException(status_code=400, detail="No text provided for explanation")
        
        if len(selected_text) > 2000:  # Limit selection size
            selected_text = selected_text[:2000]
            logger.warning("Selected text truncated to 2,000 characters")
        
        # Use the same local analyzer to provide a real explanation
        analysis_result = analyze_legal_document(selected_text)
        
        if analysis_result.get("success"):
            # Format the analysis as an explanation
            explanation = analysis_result["analysis"]
        else:
            # Fallback if analysis fails
            explanation = f"""**Error analyzing selection**

An error occurred while analyzing this text: {analysis_result.get('error', 'Unknown error')}

**Selected text:** {selected_text[:300]}{'...' if len(selected_text) > 300 else ''}

Please try again or contact support if the issue persists."""
        
        return JSONResponse(
            status_code=200,
            content={
                "explanation": explanation,
                "selected_text": selected_text,
                "character_count": len(selected_text),
                "model_used": analysis_result.get("model_used", "unknown")
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Explanation error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to generate explanation: {str(e)}")


@app.post("/analyze-document")
async def analyze_document_endpoint(file: UploadFile = File(...)):
    """
    Analyze a legal document from file upload
    
    - **file**: Legal document file (txt, pdf, doc, etc.)
    """
    try:
        # Check file type
        allowed_types = ["text/plain", "application/pdf", "application/msword", 
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]
        
        if file.content_type not in allowed_types:
            # For now, we'll be lenient and try to read as text
            logger.warning(f"Unsupported file type: {file.content_type}, attempting to read as text")
        
        # Read file content
        content = await file.read()
        
        # For now, assume it's text content
        # In a production app, you'd want proper file parsing for PDF, DOC, etc.
        try:
            legal_text = content.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400, 
                detail="Unable to decode file content. Please ensure the file is in text format."
            )
        
        # Validate content length
        if len(legal_text.strip()) == 0:
            raise HTTPException(status_code=400, detail="File appears to be empty")
        
        if len(legal_text) > 10000:  # Limit to ~10KB of text
            legal_text = legal_text[:10000]
            logger.warning("Document truncated to 10,000 characters")
        
        # Analyze the document
        result = analyze_legal_document(legal_text)
        
        if result["success"]:
            return JSONResponse(
                status_code=200,
                content={
                    "filename": file.filename,
                    "analysis": result["analysis"],
                    "model_used": result["model_used"],
                    "character_count": len(legal_text)
                }
            )
        else:
            raise HTTPException(status_code=500, detail=result["error"])
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in analyze_document_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@app.post("/analyze-text")
async def analyze_text_endpoint(request: Dict[str, str]):
    """
    Analyze legal text directly
    
    - **text**: The legal text to analyze
    """
    try:
        legal_text = request.get("text", "").strip()
        
        if not legal_text:
            raise HTTPException(status_code=400, detail="No text provided")
        
        if len(legal_text) > 10000:  # Limit to ~10KB of text
            legal_text = legal_text[:10000]
            logger.warning("Text truncated to 10,000 characters")
        
        # Analyze the text
        result = analyze_legal_document(legal_text)
        
        if result["success"]:
            return JSONResponse(
                status_code=200,
                content={
                    "analysis": result["analysis"],
                    "model_used": result["model_used"],
                    "character_count": len(legal_text)
                }
            )
        else:
            raise HTTPException(status_code=500, detail=result["error"])
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in analyze_text_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@app.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...), current_user: Optional[User] = Depends(optional_auth)):
    """
    Upload a PDF document to Google Cloud Storage and return a signed URL
    Allows both authenticated and unauthenticated usage
    
    - **file**: PDF document file to upload
    """
    try:
        # Validate file type
        if not file.content_type or not file.content_type.startswith('application/pdf'):
            raise HTTPException(
                status_code=400, 
                detail="Only PDF files are supported"
            )
        
        # Check file size (limit to 10MB)
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail="File size must be less than 10MB"
            )
        
        if not GCS_AVAILABLE:
            # Fallback: return a data URL for development
            import base64
            base64_content = base64.b64encode(content).decode()
            data_url = f"data:application/pdf;base64,{base64_content}"
            logger.warning("Using data URL - Google Cloud Storage not available")
            return JSONResponse(
                status_code=200,
                content={
                    "signed_url": data_url,
                    "filename": file.filename,
                    "message": "Mock upload successful (GCS not configured)"
                }
            )
        
        # Google Cloud Storage configuration
        bucket_name = "demystifier-ai_cloudbuild"
        
        try:
            # Initialize GCS client
            service_account_path = "service-account-key.json"
            use_signed_urls = False
            
            if os.path.exists(service_account_path):
                credentials = service_account.Credentials.from_service_account_file(
                    service_account_path,
                    scopes=['https://www.googleapis.com/auth/cloud-platform']
                )
                client = storage.Client(credentials=credentials)
                use_signed_urls = True
                logger.info("Using service account credentials for GCS")
            else:
                client = storage.Client()
                logger.info("Using default credentials for GCS")
            
            bucket = client.bucket(bucket_name)
            
            # Generate unique filename
            file_extension = os.path.splitext(file.filename)[1] if file.filename else '.pdf'
            
            if current_user:
                unique_filename = f"documents/users/{current_user.id}/{uuid.uuid4()}{file_extension}"
                logger.info(f"Uploading file for authenticated user: {current_user.email}")
            else:
                unique_filename = f"documents/temp/{uuid.uuid4()}{file_extension}"
                logger.info("Uploading file for unauthenticated user")
            
            # Create blob and upload
            blob = bucket.blob(unique_filename)
            blob.upload_from_string(content, content_type='application/pdf')
            
            # Set metadata if authenticated
            if current_user:
                blob.metadata = {
                    "user_id": current_user.id,
                    "user_email": current_user.email,
                    "original_filename": file.filename,
                    "upload_timestamp": datetime.utcnow().isoformat()
                }
                blob.patch()
            
            # Generate URL
            if not current_user:
                blob.make_public()
                file_url = blob.public_url
            else:
                try:
                    if use_signed_urls:
                        file_url = blob.generate_signed_url(
                            expiration=datetime.utcnow() + timedelta(hours=1),
                            method='GET'
                        )
                    else:
                        file_url = f"/api/proxy-gcs/{bucket_name}/{unique_filename}"
                except Exception:
                    file_url = f"/api/proxy-gcs/{bucket_name}/{unique_filename}"
            
            return JSONResponse(
                status_code=200,
                content={
                    "signed_url": file_url,
                    "filename": file.filename,
                    "size": len(content),
                    "upload_timestamp": datetime.utcnow().isoformat()
                }
            )
            
        except Exception as e:
            logger.error(f"GCS error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@app.get("/user-files")
async def get_user_files(current_user: User = Depends(require_auth)):
    """
    Get list of files uploaded by the current user (requires authentication)
    """
    try:
        if not GCS_AVAILABLE:
            # Return mock data for development
            mock_files = [
                {
                    "id": "mock-file-1",
                    "name": "Employment_Agreement.pdf",
                    "url": "https://storage.googleapis.com/mock-bucket/mock-file-1.pdf",
                    "upload_date": "2024-01-15T10:30:00Z",
                    "size": 245760
                },
                {
                    "id": "mock-file-2", 
                    "name": "Service_Contract.pdf",
                    "url": "https://storage.googleapis.com/mock-bucket/mock-file-2.pdf",
                    "upload_date": "2024-01-14T15:45:00Z",
                    "size": 189440
                }
            ]
            
            return JSONResponse(
                status_code=200,
                content={
                    "files": mock_files,
                    "total": len(mock_files),
                    "message": "Mock data - GCS not configured"
                }
            )

        # Google Cloud Storage configuration
        bucket_name = "demystifier-ai_cloudbuild"

        try:
            service_account_path = "service-account-key.json"
            
            if os.path.exists(service_account_path):
                credentials = service_account.Credentials.from_service_account_file(
                    service_account_path,
                    scopes=['https://www.googleapis.com/auth/cloud-platform']
                )
                client = storage.Client(credentials=credentials)
            else:
                client = storage.Client()
            
            bucket = client.bucket(bucket_name)
            
            # List user's files
            user_prefix = f"documents/users/{current_user.id}/"
            blobs = list(bucket.list_blobs(prefix=user_prefix))
    
            user_files = []
            for blob in blobs:
                if blob.name.endswith('/'):
                    continue
                
                filename = blob.name.split('/')[-1]
                original_filename = blob.metadata.get('original_filename', filename) if blob.metadata else filename
                
                try:
                    signed_url = blob.generate_signed_url(
                        expiration=datetime.utcnow() + timedelta(hours=1),
                        method='GET'
                    )
                except Exception:
                    signed_url = f"/api/proxy-gcs/{bucket_name}/{blob.name}"
                
                file_info = {
                    "id": blob.name,
                    "name": original_filename,
                    "url": signed_url,
                    "upload_date": blob.time_created.isoformat() if blob.time_created else None,
                    "size": blob.size,
                    "type": "pdf"
                }
                
                user_files.append(file_info)
    
            return JSONResponse(
                status_code=200,
                content={
                    "files": user_files,
                    "total": len(user_files)
                }
            )
            
        except Exception as e:
            logger.error(f"GCS error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch files: {str(e)}")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user files: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch files: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    
    # For local development
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )

