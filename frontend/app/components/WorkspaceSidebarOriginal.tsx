"use client";

import { useState, useEffect } from "react";
import { Search, Star, Clock, FileText, Plus, Upload, Settings, User, Trash2 } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";

type WorkspaceDocument = {
  id: string;
  name: string;
  url?: string;
  type?: string;
  uploadDate?: string;
  size?: number;
};

interface WorkspaceSidebarProps {
  onDocumentSelect: (document: WorkspaceDocument) => void;
}

export default function WorkspaceSidebar({ onDocumentSelect }: WorkspaceSidebarProps) {
  const { isAuthenticated, user, setShowAuthModal } = useAuth();
  const [searchQuery, setSearchQuery] = useState("");
  const [userDocuments, setUserDocuments] = useState<WorkspaceDocument[]>([]);
  const [isUploading, setIsUploading] = useState(false);

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'https://legal-backend-144935064473.asia-south1.run.app';

  // Sample documents for demo (when not authenticated)
  const sampleDocuments: WorkspaceDocument[] = [
    {
      id: "1",
      name: "Rent_Guarantee_Agreement.pdf",
      type: "PDF Document",
      uploadDate: "Just now",
      url: "https://www.adobe.com/support/products/enterprise/knowledgecenter/media/c4611_sample_explain.pdf"
    },
    {
      id: "2", 
      name: "Security_Deposit_Policy.pdf",
      type: "PDF Document",
      uploadDate: "Just now",
      url: "https://www.adobe.com/support/products/enterprise/knowledgecenter/media/c4611_sample_explain.pdf"
    }
  ];

  // Load user documents when authenticated
  useEffect(() => {
    if (isAuthenticated) {
      loadUserDocuments();
    }
  }, [isAuthenticated]);

  const loadUserDocuments = async () => {
    try {
      const token = localStorage.getItem("token");
      const response = await fetch(`${apiUrl}/user-files`, {
        headers: {
          "Authorization": `Bearer ${token}`
        }
      });

      if (response.ok) {
        const data = await response.json();
        const files = data.files || [];
        const formattedDocs = files.map((file: any) => ({
          id: file.id, // This is the full blob path from backend
          name: file.name, // This is the display name
          type: "PDF Document",
          uploadDate: file.upload_date ? new Date(file.upload_date).toLocaleDateString() : "Unknown",
          size: file.size,
          url: file.url
        }));
        setUserDocuments(formattedDocs);
      }
    } catch (error) {
      console.error("Error loading user documents:", error);
    }
  };

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    console.log('📤 Upload triggered, file:', file?.name, file?.size, file?.type);
    
    // Reset the input so the same file can be re-selected
    event.target.value = '';
    
    if (!file) {
      console.warn('📤 No file selected');
      return;
    }

    if (!isAuthenticated) {
      console.warn('📤 Not authenticated, showing auth modal');
      setShowAuthModal(true);
      return;
    }

    setIsUploading(true);
    
    try {
      const formData = new FormData();
      formData.append("file", file);

      const token = localStorage.getItem("token");
      console.log('📤 Uploading to:', `${apiUrl}/upload-pdf`, 'with token:', token ? 'present' : 'MISSING');
      
      const response = await fetch(`${apiUrl}/upload-pdf`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${token}`
        },
        body: formData
      });

      console.log('📤 Upload response status:', response.status);

      if (response.ok) {
        const result = await response.json();
        console.log('📤 Upload result:', result);
        await loadUserDocuments(); // Reload the documents list
        
        // Auto-select the newly uploaded document (prefer backend's signed_url)
        const newDoc = {
          id: result.blob_name || result.filename,
          name: result.filename,
          type: "PDF Document",
          uploadDate: "Just now",
          url: result.signed_url || result.url
        };
        console.log('📤 New document:', newDoc);
        if (newDoc.url) {
          onDocumentSelect(newDoc);
        }
      } else {
        const errorData = await response.json();
        console.error("Upload failed:", errorData);
        alert("Upload failed: " + (errorData.detail || "Unknown error"));
      }
    } catch (error) {
      console.error("Upload error:", error);
      alert("Upload error: " + error);
    } finally {
      setIsUploading(false);
    }
  };

  const handleDeleteDocument = async (docId: string, event: React.MouseEvent) => {
    event.stopPropagation(); // Prevent document selection when clicking delete
    
    if (!isAuthenticated) {
      return;
    }

    console.log("Attempting to delete document with ID:", docId);

    if (!confirm("Are you sure you want to delete this document? This action cannot be undone.")) {
      return;
    }

    try {
      const token = localStorage.getItem("token");
      console.log("Sending delete request with blob_name:", docId);
      const response = await fetch(`${apiUrl}/delete-document`, {
        method: "DELETE",
        headers: {
          "Authorization": `Bearer ${token}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ blob_name: docId })
      });

      if (response.ok) {
        await loadUserDocuments(); // Reload the documents list
        alert("Document deleted successfully");
      } else {
        const errorData = await response.json();
        console.error("Delete failed:", errorData);
        console.error("Attempted to delete blob:", docId);
        alert("Delete failed: " + (errorData.detail || "Unknown error") + "\n\nBlob path: " + docId);
      }
    } catch (error) {
      console.error("Delete error:", error);
      alert("Delete error: " + error);
    }
  };

  const documents = isAuthenticated ? userDocuments : sampleDocuments;
  const filteredDocuments = documents.filter(doc =>
    doc.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="h-full bg-gray-50 flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-gray-200 bg-white">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">Workspace</h2>
          <div className="flex items-center space-x-2">
            <button className="p-1.5 text-gray-500 hover:text-gray-700">
              <Settings className="w-4 h-4" />
            </button>
            <button className="p-1.5 text-gray-500 hover:text-gray-700">
              <User className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Search */}
        <div className="relative mb-4">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4" />
          <input
            type="text"
            placeholder="Search documents..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
          />
        </div>

        {/* New Document Button */}
        <div className="mb-4">
          <label className="block">
            <input
              type="file"
              accept=".pdf"
              onChange={handleFileUpload}
              className="hidden"
              disabled={isUploading}
            />
            <div className="w-full bg-blue-600 text-white px-4 py-2.5 rounded-lg font-medium hover:bg-blue-700 transition-colors flex items-center justify-center space-x-2 cursor-pointer">
              {isUploading ? (
                <>
                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                  <span>Uploading...</span>
                </>
              ) : (
                <>
                  <Plus className="w-4 h-4" />
                  <span>New Document</span>
                </>
              )}
            </div>
          </label>
        </div>

        {/* Upload Area */}
        <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center">
          <label className="cursor-pointer block">
            <input
              type="file"
              accept=".pdf"
              onChange={handleFileUpload}
              className="hidden"
              disabled={isUploading}
            />
            <Upload className="mx-auto h-8 w-8 text-gray-400 mb-2" />
            <p className="text-sm text-gray-600">
              {isUploading ? "Uploading..." : "Drop files here or click to upload"}
            </p>
          </label>
        </div>
      </div>

      {/* Quick Access removed */}

      {/* Recent Documents */}
      <div className="flex-1 overflow-y-auto">
        <div className="p-4">
          <div className="flex items-center space-x-2 mb-3">
            <FileText className="w-4 h-4 text-gray-500" />
            <h3 className="text-sm font-medium text-gray-700">Recent Documents</h3>
          </div>
          
          <div className="space-y-2">
            {filteredDocuments.map((doc) => (
              <div
                key={doc.id}
                onClick={() => onDocumentSelect(doc)}
                className="flex items-start space-x-3 p-3 rounded-lg hover:bg-gray-100 cursor-pointer group"
              >
                <div className="w-8 h-8 bg-red-100 rounded flex items-center justify-center flex-shrink-0">
                  <FileText className="w-4 h-4 text-red-600" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">
                    {doc.name}
                  </p>
                  <p className="text-xs text-gray-500">
                    {doc.type} • {doc.uploadDate}
                  </p>
                </div>
                {isAuthenticated && (
                  <button
                    onClick={(e) => handleDeleteDocument(doc.id, e)}
                    className="opacity-0 group-hover:opacity-100 p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-all"
                    title="Delete document"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            ))}
          </div>

          {!isAuthenticated && (
            <div className="mt-4 p-3 bg-blue-50 border border-blue-200 rounded-lg">
              <p className="text-xs text-blue-800">
                <strong>Demo Mode:</strong> Sign in to upload and manage your own documents.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}