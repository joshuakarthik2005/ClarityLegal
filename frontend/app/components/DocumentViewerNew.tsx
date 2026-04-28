"use client";

import { useState, useEffect, useRef } from "react";
import { ZoomIn, ZoomOut, RotateCw, Download, FileText, Loader2 } from "lucide-react";

interface DocumentViewerProps {
  documentUrl: string;
  filename: string;
  onTextSelection?: (selectedText: string) => void;
  onExplainText?: (text: string) => void;
  onRagSearch?: (query: string) => void;
  onViewerReady?: (searchFunction: (text: string) => void) => void;
}

export default function DocumentViewer({ 
  documentUrl, 
  filename, 
  onTextSelection,
  onExplainText,
  onRagSearch,
  onViewerReady
}: DocumentViewerProps) {
  const [zoom, setZoom] = useState(100);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedText, setSelectedText] = useState("");
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    setIsLoading(true);
    setError(null);
  }, [documentUrl]);

  const handleIframeLoad = () => {
    setIsLoading(false);
    setError(null);
  };

  const handleIframeError = () => {
    setIsLoading(false);
    setError("Failed to load document. The document may be corrupted or the link may have expired.");
  };

  // Handle text selection using a simplified approach
  const handleTextSelection = () => {
    try {
      const selection = window.getSelection();
      if (selection && selection.toString().trim()) {
        const text = selection.toString().trim();
        setSelectedText(text);
        if (onTextSelection) onTextSelection(text);
        if (onExplainText) onExplainText(text);
        if (onRagSearch) onRagSearch(text);
      }
    } catch (error) {
      console.warn("Text selection not available:", error);
    }
  };

  const handleDownload = () => {
    const link = document.createElement('a');
    link.href = documentUrl;
    link.download = filename || 'document.pdf';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="h-full flex flex-col bg-white border border-gray-200 rounded-lg shadow-card overflow-hidden">
      {/* Document Viewer Header */}
      <div className="flex-shrink-0 border-b border-gray-200 px-4 py-3 bg-gray-50">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <FileText className="w-4 h-4 text-primary-blue" />
            <div>
              <span className="text-sm font-medium text-gray-900">{filename}</span>
              <div className="text-xs text-gray-500">PDF Document</div>
            </div>
          </div>
          
          <div className="flex items-center space-x-1">
            <button
              onClick={() => setZoom(Math.max(50, zoom - 25))}
              className="p-2 hover:bg-gray-200 rounded-md transition-colors group"
              title="Zoom out"
            >
              <ZoomOut className="w-4 h-4 text-gray-600 group-hover:text-gray-800" />
            </button>
            
            <span className="text-sm text-gray-600 min-w-[3rem] text-center font-medium">{zoom}%</span>
            
            <button
              onClick={() => setZoom(Math.min(200, zoom + 25))}
              className="p-2 hover:bg-gray-200 rounded-md transition-colors group"
              title="Zoom in"
            >
              <ZoomIn className="w-4 h-4 text-gray-600 group-hover:text-gray-800" />
            </button>
            
            <div className="w-px h-4 bg-gray-300 mx-2" />
            
            <button 
              className="p-2 hover:bg-gray-200 rounded-md transition-colors group" 
              title="Rotate"
            >
              <RotateCw className="w-4 h-4 text-gray-600 group-hover:text-gray-800" />
            </button>
            
            <button 
              onClick={handleDownload}
              className="p-2 hover:bg-gray-200 rounded-md transition-colors group" 
              title="Download"
            >
              <Download className="w-4 h-4 text-gray-600 group-hover:text-gray-800" />
            </button>
          </div>
        </div>
      </div>

      {/* Document Content */}
      <div className="flex-1 relative overflow-hidden">
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-gray-50 z-10">
            <div className="text-center">
              <Loader2 className="w-8 h-8 text-accent animate-spin mx-auto mb-3" />
              <p className="text-sm text-gray-600">Loading document...</p>
            </div>
          </div>
        )}

        {error && (
          <div className="absolute inset-0 flex items-center justify-center bg-gray-50 z-10">
            <div className="text-center max-w-md p-6">
              <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4">
                <FileText className="w-8 h-8 text-red-600" />
              </div>
              <h3 className="text-lg font-semibold text-gray-900 mb-2">Document Load Error</h3>
              <p className="text-sm text-gray-600 mb-4">{error}</p>
              <button 
                onClick={() => window.location.reload()}
                className="bg-accent text-white px-4 py-2 rounded-lg hover:bg-accent/90 transition-colors"
              >
                Try Again
              </button>
            </div>
          </div>
        )}

        {/* PDF Viewer */}
        <div className="w-full h-full">
          <iframe
            ref={iframeRef}
            src={`${documentUrl}#toolbar=1&navpanes=1&scrollbar=1&page=1&zoom=${zoom}`}
            className="w-full h-full border-0"
            title={filename}
            onLoad={handleIframeLoad}
            onError={handleIframeError}
            onMouseUp={handleTextSelection}
            style={{
              transform: `scale(${zoom / 100})`,
              transformOrigin: 'top left',
              width: `${100 / (zoom / 100)}%`,
              height: `${100 / (zoom / 100)}%`
            }}
          />
        </div>

        {/* Selection Overlay */}
        {selectedText && (
          <div className="absolute top-4 left-4 right-4 bg-accent/10 border border-accent/20 rounded-lg p-3 backdrop-blur-sm">
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <p className="text-xs font-medium text-accent mb-1">Selected Text:</p>
                <p className="text-sm text-gray-700 line-clamp-2">
                  "{selectedText.length > 100 ? selectedText.substring(0, 100) + "..." : selectedText}"
                </p>
              </div>
              <button
                onClick={() => setSelectedText("")}
                className="ml-3 text-gray-400 hover:text-gray-600 transition-colors"
              >
                ×
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Instructions */}
      <div className="flex-shrink-0 border-t border-gray-200 px-4 py-2 bg-gray-50">
        <p className="text-xs text-gray-500 text-center">
          💡 Highlight any text in the document to get instant AI explanations
        </p>
      </div>
    </div>
  );
}