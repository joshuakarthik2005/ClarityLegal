"use client";

import { useState, useEffect, useRef } from "react";
import ReactDOM from "react-dom";
import { FileText, MessageSquare, X, Send, Bot, User } from "lucide-react";
import { API_CONFIG, getApiUrl } from "../config/api";
import { getAuthToken, withAuthHeaders } from "../utils/auth";

// Adobe PDF Embed API Configuration
// Now uses environment variable to support multiple environments/machines
// Set NEXT_PUBLIC_ADOBE_CLIENT_ID in .env.local
const ADOBE_API_KEY = process.env.NEXT_PUBLIC_ADOBE_CLIENT_ID || "f2e8d4ce187f428f99592da250de530b";

// Debug logging for troubleshooting (will show in browser console)
if (typeof window !== 'undefined') {
  console.log('🔑 Adobe PDF Embed - Using Client ID:', ADOBE_API_KEY);
  console.log('🌐 Current Origin:', window.location.origin);
  console.log('💡 Make sure this origin is added to Adobe Console allowed domains');
}

interface DocumentViewerProps {
  documentUrl: string;
  filename: string;
  onExplainText: (text: string) => void;
  onRagSearch?: (query: string) => void;
  onViewerReady?: (searchFunction: (text: string) => void) => void;
}

interface ExplainTooltipProps {
  x: number;
  y: number;
  selectedText: string;
  onExplain: (text: string) => void;
  onClose: () => void;
  position?: "fixed" | "absolute";
  bounds?: { width: number; height: number };
}

interface ChatMessage {
  id: string;
  text: string;
  isUser: boolean;
  timestamp: Date;
}

declare global {
  interface Window {
    AdobeDC: any;
  }
}

function ExplainTooltip({ x, y, selectedText, onExplain, onClose, position = "fixed", bounds }: ExplainTooltipProps) {
  const tooltipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (tooltipRef.current && !tooltipRef.current.contains(event.target as Node)) {
        onClose();
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [onClose]);

  // Compute safe positions (avoid edges)
  const maxW = bounds?.width ?? window.innerWidth;
  const maxH = bounds?.height ?? window.innerHeight;
  const safeLeft = Math.max(12, Math.min(x, maxW - 12));
  const safeTop = Math.max(12, Math.min(y + 8, maxH - 12));

  return (
    <div 
      ref={tooltipRef}
      className={`${position === 'absolute' ? 'absolute' : 'fixed'} z-50 bg-white border border-gray-200 rounded-lg shadow-lg p-3 max-w-xs animate-in fade-in duration-200 transform -translate-x-1/2`}
      style={{ left: safeLeft, top: safeTop }}
    >
      <div className="flex items-start justify-between mb-2">
        <div className="text-xs text-gray-600 line-clamp-3 pr-2">
          "{selectedText.length > 100 ? selectedText.substring(0, 100) + "..." : selectedText}"
        </div>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-600 transition-colors"
        >
          <X className="w-3 h-3" />
        </button>
      </div>
      <div className="flex space-x-2">
        <button
          onClick={() => {
            onExplain(selectedText);
            onClose();
          }}
          className="flex items-center space-x-1 bg-blue-600 text-white px-3 py-1.5 rounded-md text-sm font-medium hover:bg-blue-700 transition-colors shadow-sm"
        >
          <MessageSquare className="w-3 h-3" />
          <span>Explain this</span>
        </button>
        <button
          onClick={onClose}
          className="px-3 py-1.5 text-gray-600 hover:bg-gray-100 rounded-md text-sm transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

const DocumentViewer = ({ documentUrl, filename, onExplainText, onRagSearch, onViewerReady }: DocumentViewerProps) => {
  const viewerRef = useRef<HTMLDivElement>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string>("");
  const [adobeView, setAdobeView] = useState<any>(null);
  const [selectedText, setSelectedText] = useState("");
  const [tooltipPosition, setTooltipPosition] = useState<{ x: number; y: number } | null>(null);
  const [adobeViewer, setAdobeViewer] = useState<any>(null);
  
  // Chat-related states
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [currentMessage, setCurrentMessage] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const [documentText, setDocumentText] = useState(""); // Store document text for context

  // Use a static ID since we only have one viewer on the page at a time.
  // This avoids any weird DOM querySelector issues with React 18's useId().
  const viewerId = 'adobe-dc-view-container';
  const initInProgressRef = useRef(false);
  const sdkReadyRef = useRef(false);
  const selectionIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const lastExplainedRef = useRef<string>("");

  const closeTooltip = () => {
    setTooltipPosition(null);
    setSelectedText("");
  };

  const handleExplainText = (text: string) => {
    onExplainText(text);
    // Also kick off a RAG search for related snippets if callback provided
    try {
      if (typeof (onRagSearch) === 'function' && text && text.trim()) {
        onRagSearch(text.trim());
      }
    } catch (e) {
      // non-fatal
      console.warn('onRagSearch callback failed:', e);
    }
    closeTooltip();
  };

  const extractDocumentText = async () => {
    try {
      // Normalize URL so backend can fetch it successfully
      const toAbsoluteBackendUrl = (input: string): string => {
        try {
          const u = new URL(input, window.location.origin);
          // If it's our frontend-relative proxy route, rewrite to backend absolute
          if (u.pathname.startsWith('/api/proxy-gcs/')) {
            const rest = u.pathname.replace(/^\/api\/proxy-gcs\//, '');
            return `${API_CONFIG.BASE_URL}/proxy-gcs/${rest}`;
          }
          // If it's a bare relative backend proxy path
          if (u.pathname.startsWith('/proxy-gcs/')) {
            const rest = u.pathname.replace(/^\/proxy-gcs\//, '');
            return `${API_CONFIG.BASE_URL}/proxy-gcs/${rest}`;
          }
          return u.toString();
        } catch {
          // Fallback best-effort
          if (input.startsWith('/api/proxy-gcs/')) {
            const rest = input.replace(/^\/api\/proxy-gcs\//, '');
            return `${API_CONFIG.BASE_URL}/proxy-gcs/${rest}`;
          }
          if (input.startsWith('/proxy-gcs/')) {
            const rest = input.replace(/^\/proxy-gcs\//, '');
            return `${API_CONFIG.BASE_URL}/proxy-gcs/${rest}`;
          }
          return input;
        }
      };

      const absoluteUrl = toAbsoluteBackendUrl(documentUrl);
      console.log('Extracting document text from:', absoluteUrl);
      const response = await fetch(getApiUrl(API_CONFIG.ENDPOINTS.EXTRACT_PDF_TEXT), {
        method: 'POST',
        headers: await withAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ url: absoluteUrl }),
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      console.log('Document text extracted:', data.text?.length || 0, 'characters');
      setDocumentText(data.text || '');
    } catch (error) {
      console.error('Error extracting document text:', error);
    }
  };

  const sendChatMessage = async (message: string) => {
    console.log('sendChatMessage called with:', message);
    
    if (!message.trim()) return;

    // Add user message immediately
    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      text: message.trim(),
      isUser: true,
      timestamp: new Date(),
    };

    setChatMessages(prev => {
      const newMessages = [...prev, userMessage];
      console.log('Updated chat messages after user message:', newMessages);
      return newMessages;
    });

    setIsTyping(true);

    try {
      console.log('Sending request to chat API...');
      const authHeaders = await withAuthHeaders({ 'Content-Type': 'application/json' });
      const response = await fetch(getApiUrl(API_CONFIG.ENDPOINTS.CHAT), {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({
          message: message.trim(),
          document_text: documentText,
        }),
      });

      console.log('Chat API response status:', response.status);

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      console.log('Chat API response data:', data);

      const aiMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        text: data.response || 'I received your message but couldn\'t generate a response.',
        isUser: false,
        timestamp: new Date(),
      };

      setChatMessages(prev => [...prev, aiMessage]);
    } catch (error) {
      console.error('Error sending chat message:', error);
      const errorMessage: ChatMessage = {
        id: (Date.now() + 1).toString(),
        text: 'Sorry, I encountered an error. Please try again.',
        isUser: false,
        timestamp: new Date(),
      };
      setChatMessages(prev => [...prev, errorMessage]);
    } finally {
      setIsTyping(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage(currentMessage);
      setCurrentMessage('');
    }
  };

  // Chat functions
  // Cleanup effect
  useEffect(() => {
    return () => {
      if (selectionIntervalRef.current) {
        clearInterval(selectionIntervalRef.current);
      }
    };
  }, []);

  useEffect(() => {
    // Dispose previous instance
    if (adobeView) {
      try {
        initInProgressRef.current = false;
        adobeView.dispose?.();
      } catch (e) {
        console.warn("Error disposing Adobe viewer:", e);
      }
      setAdobeView(null);
    }

    // Clear container
    if (viewerRef.current) viewerRef.current.innerHTML = "";

    setIsLoading(true);
    setError("");

    const startWhenReady = () => {
      sdkReadyRef.current = true;
      initializeAdobePDF();
    };

    // Load SDK once and wait for ready event
    if (window.AdobeDC) {
      startWhenReady();
    } else {
      const handleReady = () => {
        document.removeEventListener("adobe_dc_view_sdk.ready", handleReady);
        startWhenReady();
      };
      document.addEventListener("adobe_dc_view_sdk.ready", handleReady);

      // Inject script if not present
      if (!document.querySelector('script[src*="documentservices.adobe.com/view-sdk/viewer.js"]')) {
        const script = document.createElement("script");
        script.src = "https://documentservices.adobe.com/view-sdk/viewer.js";
        script.async = true;
        script.onerror = () => {
          setError("Failed to load Adobe PDF viewer script");
          setIsLoading(false);
        };
        document.head.appendChild(script);
      }
    }

    return () => {
      // No-op; we removed the handler above when it fired. If it didn't fire, browser GC will collect it with component unmount.
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentUrl, filename]);

  const initializeAdobePDF = async () => {
    if (!window.AdobeDC || !viewerRef.current) return;
    if (initInProgressRef.current) return;
    
    initInProgressRef.current = true;
    try {
      // Build the fetch URL — route cross-origin URLs through our proxy
      let fetchUrl = documentUrl;
      let fetchHeaders: HeadersInit = {};
      try {
        const u = new URL(documentUrl, window.location.origin);
        // Rewrite local relative /api/proxy-gcs path to backend absolute for proxying
        if (u.pathname.startsWith('/api/proxy-gcs/')) {
          const rest = u.pathname.replace(/^\/api\/proxy-gcs\//, '');
          const backendUrl = `${API_CONFIG.BASE_URL}/proxy-gcs/${rest}`;
          fetchUrl = `/api/proxy-pdf?url=${encodeURIComponent(backendUrl)}`;
          fetchHeaders = await withAuthHeaders({});
        } else if (u.pathname.startsWith('/proxy-gcs/')) {
          const rest = u.pathname.replace(/^\/proxy-gcs\//, '');
          const backendUrl = `${API_CONFIG.BASE_URL}/proxy-gcs/${rest}`;
          fetchUrl = `/api/proxy-pdf?url=${encodeURIComponent(backendUrl)}`;
          fetchHeaders = await withAuthHeaders({});
        } else {
          const isCrossOrigin = u.origin !== window.location.origin;
          if (isCrossOrigin) {
            fetchUrl = `/api/proxy-pdf?url=${encodeURIComponent(u.toString())}`;
            fetchHeaders = await withAuthHeaders({});
          }
        }
      } catch {
        // If it isn't a valid absolute/relative URL, try as-is
        fetchHeaders = await withAuthHeaders({});
      }

      console.log('📥 Adobe viewer fetching PDF from:', fetchUrl);

      // Adobe SDK requires a Promise that resolves with an ArrayBuffer.
      // IMPORTANT: We must use the explicit Promise constructor so Adobe
      // can subscribe to the .then() BEFORE it resolves. An already-resolved
      // Promise (from an IIFE) may be missed by the SDK.
      const filePromise = new Promise<ArrayBuffer>((resolve, reject) => {
        fetch(fetchUrl, { headers: fetchHeaders })
          .then(res => {
            if (!res.ok) {
              reject(new Error(`Failed to fetch PDF: ${res.status}`));
              return;
            }
            return res.arrayBuffer();
          })
          .then(buffer => {
            if (buffer) {
              console.log('✅ PDF fetched successfully, size:', buffer.byteLength, 'bytes');
              resolve(buffer);
            }
          })
          .catch(reject);
      });

      // Create new view
      const adobeDCView = new window.AdobeDC.View({
        clientId: ADOBE_API_KEY,
        divId: viewerId
      });

      const safeFileName = filename.toLowerCase().endsWith('.pdf') ? filename : `${filename}.pdf`;
      console.log('📄 Previewing file:', safeFileName, 'with client ID:', ADOBE_API_KEY);
      const previewFilePromise = adobeDCView.previewFile(
        {
          content: { promise: filePromise },
          metaData: { fileName: safeFileName }
        },
        {
          embedMode: "SIZED_CONTAINER",
          defaultViewMode: "FIT_PAGE",
          showZoomControl: true,
          showPageControls: true,
          dockPageControls: true,
          showLeftHandPanel: true,
          showThumbnails: true,
          showBookmarks: true,
          showAnnotationTools: false,
          showDownloadPDF: true,
          showPrintPDF: true,
          enableFormFilling: true,
          includePDFAnnotations: true,
          enableFilePreviewEvents: true,
          focusOnRendering: false,
          showDisabledSaveButton: false
        }
      );

      // Wait for the preview to be ready and get access to viewer APIs
      previewFilePromise.then(adobeViewer => {
        initInProgressRef.current = false;
        setAdobeViewer(adobeViewer);
        setIsLoading(false);
        
        // Extract document text for chat context
        extractDocumentText();
        
        // Set up text selection monitoring
        adobeViewer.getAPIs().then((apis: any) => {
          // Helper: find the page number containing the given text by scanning the PDF with pdfjs
          const findPageNumberForText = async (pdfUrl: string, searchText: string): Promise<number | null> => {
            try {
              // Use the proxy to ensure same-origin fetch and avoid CORS
              // Attach bearer token so the proxy can authenticate to backend-protected URLs
              let bearer = '';
              try { bearer = await getAuthToken(); } catch { /* optional */ }
              const targetUrl = `/api/proxy-pdf?url=${encodeURIComponent(pdfUrl)}${bearer ? `&bearer=${encodeURIComponent(bearer)}` : ''}`;

              // Lazy-load pdfjs only in the browser
              const pdfjsLib: any = await import('pdfjs-dist');
              // Configure workerSrc robustly across environments
              if (pdfjsLib && pdfjsLib.GlobalWorkerOptions) {
                try {
                  const workerMod: any = await import('pdfjs-dist/build/pdf.worker.min.js');
                  (pdfjsLib as any).GlobalWorkerOptions.workerSrc = workerMod?.default || workerMod;
                } catch {
                  // Fallback to CDN if bundling the worker fails
                  const ver = (pdfjsLib as any).version || '3.11.174';
                  (pdfjsLib as any).GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${ver}/pdf.worker.min.js`;
                }
              }

              const loadingTask = (pdfjsLib as any).getDocument({ url: targetUrl });
              const pdf = await loadingTask.promise;

              // Prepare progressive search terms
              const normalized = (s: string) => s.replace(/\s+/g, ' ').trim().toLowerCase();
              const candidates = [
                searchText.substring(0, 200),
                searchText.substring(0, 150),
                searchText.substring(0, 100),
                searchText.substring(0, 60),
                searchText.split(' ').slice(0, 8).join(' '),
                searchText.split(' ').slice(0, 5).join(' ')
              ]
                .map(t => normalized(t))
                .filter(t => t.length >= 8);

              for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
                const page = await pdf.getPage(pageNum);
                const textContent = await page.getTextContent();
                const pageText = normalized(textContent.items.map((it: any) => it.str).join(' '));

                for (const cand of candidates) {
                  if (pageText.includes(cand)) {
                    return pageNum;
                  }
                }
              }

              return null;
            } catch (err) {
              console.warn('PDF text scan failed:', err);
              return null;
            }
          };
          
          // Create search function and pass to parent
          const searchInPDF = async (searchText: string) => {
            if (!searchText) {
              console.log('No search text provided');
              return;
            }
            
            try {
              console.log('🔎 Searching PDF for snippet, length:', searchText.length);
              // 1) Try scanning the PDF to find the page number
              const pageNumber = await findPageNumberForText(documentUrl, searchText);
              if (pageNumber) {
                console.log('📄 Navigating PDF to page', pageNumber);
                try {
                  await apis.gotoLocation({ pageNumber, zoom: 125 });
                } catch (navErr) {
                  console.warn('gotoLocation failed, trying fallback scroll', navErr);
                  // Fallback: focus the iframe so user can Ctrl+F manually
                  const pdfContainer = document.getElementById(viewerId);
                  const iframe = pdfContainer?.querySelector('iframe') as HTMLIFrameElement | null;
                  iframe?.focus();
                  alert(`Moved to page ${pageNumber}. Use Ctrl+F and paste the snippet to highlight it.`);
                }
                return;
              }

              // 2) If page not found via PDF scan, give a helpful fallback
              console.warn('❌ Could not locate text via PDF scan');
              try {
                await navigator.clipboard.writeText(searchText);
              } catch {}
              alert('Could not automatically locate the text. The snippet has been copied to your clipboard — press Ctrl+F in the PDF and paste to find it.');
            } catch (e) {
              console.error('PDF search error:', e);
            }
          };
          
          // Notify parent that viewer is ready with search function
          if (onViewerReady) {
            console.log('✅ Adobe viewer ready. Passing search function to parent.');
            onViewerReady(searchInPDF);
          }
          
          // Set up a periodic check for selected content
          const checkSelection = () => {
            apis.getSelectedContent()
              .then((result: any) => {
                if (result && result.data && result.data.trim()) {
                  const text = result.data.trim();
                  if (text && text !== lastExplainedRef.current) {
                    lastExplainedRef.current = text;
                    setSelectedText(text);
                    handleExplainText(text);
                  }
                } else {
                  // No selection
                  setTooltipPosition(null);
                  setSelectedText("");
                }
              })
              .catch(() => {
                // API call failed, probably no selection
                setTooltipPosition(null);
                setSelectedText("");
              });
          };

          // Check for selection changes every 500ms
          selectionIntervalRef.current = setInterval(checkSelection, 500);
        });
      });

      // Register event listeners for file preview events
      const eventOptions = {
        enableFilePreviewEvents: true,
        listenOn: [
          window.AdobeDC.View.Enum.FilePreviewEvents.PREVIEW_SELECTION_END,
          window.AdobeDC.View.Enum.FilePreviewEvents.PREVIEW_PAGE_CLICK,
          window.AdobeDC.View.Enum.FilePreviewEvents.PREVIEW_DOCUMENT_CLICK
        ]
      };

      adobeDCView.registerCallback(
        window.AdobeDC.View.Enum.CallbackType.EVENT_LISTENER,
        function(event: any) {
          try {
            console.log("Adobe PDF Event:", event?.type, event?.data);
            if (!event || !event.type) return;
            
            if (event.type === 'PREVIEW_SELECTION_END' && event.data?.selections) {
            const selection = window.getSelection();
            const text = selection?.toString().trim() || "";
            if (text && text !== lastExplainedRef.current) {
              lastExplainedRef.current = text;
              setSelectedText(text);
              handleExplainText(text);
            }
            } else if (event.type === 'PREVIEW_PAGE_CLICK' || event.type === 'PREVIEW_DOCUMENT_CLICK') {
            // Clear selection when clicking elsewhere
            setTimeout(() => {
              const selection = window.getSelection();
              if (!selection || !selection.toString().trim()) {
                setTooltipPosition(null);
                setSelectedText("");
              }
            }, 100);
            }
          } catch (eh) {
            // Guard against SDK edge cases
            console.warn('Adobe event handler error:', eh);
          }
        },
        eventOptions
      );

      setAdobeView(adobeDCView);
      
      // Hide unwanted toolbar icons (Settings and User profile)
      setTimeout(() => {
        const style = document.createElement('style');
        style.id = 'adobe-toolbar-custom-styles';
        // Remove any existing style with this ID
        const existingStyle = document.getElementById('adobe-toolbar-custom-styles');
        if (existingStyle) {
          existingStyle.remove();
        }
        style.textContent = `
          /* Hide Settings icon */
          button[title="Settings"],
          button[aria-label="Settings"],
          .adobe-dc-view-sdk-btn[title*="Settings"],
          /* Hide User profile icon */
          button[title="Profile"],
          button[aria-label="Profile"],
          .adobe-dc-view-sdk-btn[title*="Profile"],
          /* Hide any account/user related buttons */
          button[title*="Account"],
          button[aria-label*="Account"] {
            display: none !important;
          }
        `;
        document.head.appendChild(style);
      }, 1000);
      
    } catch (err) {
      console.error("Adobe PDF initialization error:", err);
      const errorMsg = err instanceof Error ? err.message : "Failed to initialize PDF viewer";
      
      if (errorMsg.includes('not authorized') || errorMsg.includes('domain')) {
        setError(
          `Domain Authorization Error: The current origin (${window.location.origin}) is not authorized for this Adobe Client ID. ` +
          `Please add this origin to the Adobe PDF Embed API Console under "Allowed Domains".`
        );
      } else {
        setError(errorMsg);
      }
      setIsLoading(false);
      initInProgressRef.current = false;
    }
  };

  if (error) {
    return (
      <div className="h-full flex flex-col">
        <div className="flex-1 flex items-center justify-center bg-gray-50">
          <div className="text-center">
            <FileText className="w-16 h-16 text-gray-300 mx-auto mb-4" />
            <p className="text-gray-500 mb-2">Failed to load document</p>
            <p className="text-sm text-gray-400">{error}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex bg-white">
      {/* PDF Viewer Container */}
      <div className="flex-1 relative overflow-hidden">
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-gray-50 z-10">
            <div className="text-center">
              <FileText className="w-16 h-16 text-gray-300 mx-auto mb-4" />
              <p className="text-xl font-semibold text-gray-700 mb-2">Preparing Document</p>
              <p className="text-sm text-gray-500">Please wait while we load your PDF...</p>
            </div>
          </div>
        )}
        
        <div 
          id={viewerId}
          ref={viewerRef}
          className="w-full h-full adobe-pdf-container"
          style={{ minHeight: '600px' }}
        />
        
        <style jsx global>{`
          /* Hide Adobe PDF viewer toolbar icons */
          #${viewerId} button[title="Settings"],
          #${viewerId} button[aria-label="Settings"],
          #${viewerId} button[title="Profile"],
          #${viewerId} button[aria-label="Profile"],
          #${viewerId} .settings-button,
          #${viewerId} .profile-button,
          .adobe-pdf-container button[title="Settings"],
          .adobe-pdf-container button[aria-label="Settings"],
          .adobe-pdf-container button[title="Profile"],
          .adobe-pdf-container button[aria-label="Profile"] {
            display: none !important;
            visibility: hidden !important;
            opacity: 0 !important;
            pointer-events: none !important;
          }
        `}</style>

        {/* Explain Tooltip removed: auto-trigger explain and RAG on selection */}

        

      {/* Chat Panel */}
      <div className={`transition-all duration-300 ease-in-out bg-gray-50 border-l border-gray-200 ${isChatOpen ? 'w-96' : 'w-0'} overflow-hidden`}>
        {isChatOpen && (
          <div className="h-full flex flex-col">
            <div className="p-4 border-b border-gray-200 bg-white">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-gray-900">Chat with Document</h3>
                <button
                  onClick={() => setIsChatOpen(false)}
                  className="text-gray-400 hover:text-gray-600 transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {chatMessages.length === 0 && (
                <div className="text-center py-8">
                  <Bot className="w-12 h-12 text-gray-300 mx-auto mb-3" />
                  <p className="text-sm text-gray-500">Start a conversation about the document</p>
                  <p className="text-xs text-gray-400 mt-1">Ask questions, request summaries, or get explanations</p>
                </div>
              )}

              {chatMessages.map((message) => (
                <div
                  key={message.id}
                  className={`flex ${message.isUser ? 'justify-end' : 'justify-start'}`}
                >
                  <div className={`flex items-start space-x-2 max-w-xs lg:max-w-md ${message.isUser ? 'flex-row-reverse space-x-reverse' : ''}`}>
                    <div className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${message.isUser ? 'bg-blue-600' : 'bg-gray-600'}`}>
                      {message.isUser ? (
                        <User className="w-4 h-4 text-white" />
                      ) : (
                        <Bot className="w-4 h-4 text-white" />
                      )}
                    </div>
                    <div className={`px-3 py-2 rounded-lg ${message.isUser ? 'bg-blue-600 text-white' : 'bg-white text-gray-900 border border-gray-200'}`}>
                      <p className="text-sm">{message.text}</p>
                      <p className={`text-xs mt-1 ${message.isUser ? 'text-blue-100' : 'text-gray-500'}`}>
                        {message.timestamp.toLocaleTimeString()}
                      </p>
                    </div>
                  </div>
                </div>
              ))}

              {isTyping && (
                <div className="flex justify-start">
                  <div className="flex items-start space-x-2 max-w-xs">
                    <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gray-600 flex items-center justify-center">
                      <Bot className="w-4 h-4 text-white" />
                    </div>
                    <div className="px-3 py-2 rounded-lg bg-white border border-gray-200">
                      <div className="flex space-x-1">
                        <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"></div>
                        <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.1s' }}></div>
                        <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></div>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="p-4 border-t border-gray-200 bg-white">
              <button
                onClick={() => {
                  const testMessage = "Test message: " + new Date().toLocaleTimeString();
                  setChatMessages(prev => [...prev, {
                    id: Date.now().toString(),
                    text: testMessage,
                    isUser: true,
                    timestamp: new Date()
                  }]);
                }}
                className="w-full mb-2 px-3 py-1 bg-red-600 text-white rounded text-sm hover:bg-red-700"
              >
                Test Add Message
              </button>
              <div className="flex space-x-2">
                <input
                  type="text"
                  placeholder="Ask about this document..."
                  value={currentMessage}
                  onChange={(e) => setCurrentMessage(e.target.value)}
                  onKeyPress={handleKeyPress}
                  className="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  disabled={isTyping}
                />
                <button
                  onClick={() => {
                    sendChatMessage(currentMessage);
                    setCurrentMessage('');
                  }}
                  disabled={!currentMessage.trim() || isTyping}
                  className="px-3 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Send className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
      </div>
    </div>
  );
};

export default DocumentViewer;
