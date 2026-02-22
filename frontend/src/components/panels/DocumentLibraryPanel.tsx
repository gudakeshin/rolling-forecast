import { useState, useCallback, useRef } from 'react';
import {
  Upload, Link2, Search, Trash2, FileText, FileSpreadsheet,
  Image, Globe, File, Loader2, CheckCircle, XCircle,
  ChevronDown, ChevronUp, Filter, FolderOpen, Plus,
  ExternalLink, Tag, Clock, Database, AlertCircle,
} from 'lucide-react';
import { apiPost, apiDelete, apiGet } from '../../api/client';

const FILE_ICONS: Record<string, typeof FileText> = {
  pdf: FileText,
  docx: FileText,
  doc: FileText,
  pptx: FileSpreadsheet,
  xlsx: FileSpreadsheet,
  xls: FileSpreadsheet,
  csv: FileSpreadsheet,
  txt: File,
  md: File,
  html: Globe,
  htm: Globe,
  url: Globe,
};

const STATUS_STYLES: Record<string, string> = {
  ready: 'bg-deloitte-green/10 text-deloitte-green border-deloitte-green/30',
  processing: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
  failed: 'bg-red-500/10 text-red-400 border-red-500/30',
};

const SCOPE_LABELS: Record<string, string> = {
  user: 'My Documents',
  conversation: 'This Chat',
  global: 'Global',
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

interface DocItem {
  id: string;
  original_name: string;
  file_type: string;
  scope: string;
  status: string;
  chunk_count: number;
  total_chars: number;
  file_size_bytes: number;
  description: string | null;
  tags: string[];
  source_url: string | null;
  created_at: string | null;
}

interface SearchResult {
  chunk_id: string;
  content: string;
  score: number;
  document_id: string;
  original_name: string;
  file_type: string;
  chunk_index: number;
}

export function DocumentLibraryPanel({ data }: { data: any }) {
  const d = data?.data || data;
  const initialDocs: DocItem[] = d?.documents || [];
  const summary = d?.summary || {};

  const [documents, setDocuments] = useState<DocItem[]>(initialDocs);
  const [activeTab, setActiveTab] = useState<'documents' | 'upload' | 'search'>('documents');
  const [scopeFilter, setScopeFilter] = useState<string>('all');
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);

  // URL ingestion
  const [urlInput, setUrlInput] = useState('');
  const [isIngesting, setIsIngesting] = useState(false);

  // Search
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  const [expandedDoc, setExpandedDoc] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const filteredDocs = documents.filter(doc => {
    if (scopeFilter !== 'all' && doc.scope !== scopeFilter) return false;
    if (typeFilter !== 'all' && doc.file_type !== typeFilter) return false;
    return true;
  });

  const uniqueTypes = [...new Set(documents.map(d => d.file_type))];

  const refreshDocuments = useCallback(async () => {
    try {
      const res = await apiGet<{ documents: DocItem[] }>('/context/documents');
      setDocuments(res.documents);
    } catch { /* ignore */ }
  }, []);

  const handleFileUpload = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setIsUploading(true);
    setUploadError(null);
    setUploadSuccess(null);

    let successCount = 0;
    let failCount = 0;

    for (const file of Array.from(files)) {
      try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('scope', 'user');
        const res = await apiPost<any>('/context/upload', formData);
        if (res.status === 'ready') {
          successCount++;
        } else if (res.status === 'failed') {
          failCount++;
          setUploadError(res.error_message || `Failed to process ${file.name}`);
        } else {
          successCount++;
        }
      } catch (e: any) {
        failCount++;
        setUploadError(e.message || 'Upload failed');
      }
    }

    setIsUploading(false);
    if (successCount > 0) {
      setUploadSuccess(`Uploaded ${successCount} file${successCount > 1 ? 's' : ''} successfully`);
    }
    await refreshDocuments();
    setTimeout(() => { setUploadSuccess(null); setUploadError(null); }, 5000);
  }, [refreshDocuments]);

  const handleUrlIngest = useCallback(async () => {
    if (!urlInput.trim()) return;
    setIsIngesting(true);
    setUploadError(null);
    try {
      await apiPost('/context/ingest-url', { url: urlInput.trim(), scope: 'user' });
      setUrlInput('');
      setUploadSuccess('URL content indexed successfully');
      await refreshDocuments();
      setTimeout(() => setUploadSuccess(null), 4000);
    } catch (e: any) {
      setUploadError(e.message || 'URL ingestion failed');
    } finally {
      setIsIngesting(false);
    }
  }, [urlInput, refreshDocuments]);

  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) return;
    setIsSearching(true);
    try {
      const res = await apiPost<{ results: SearchResult[] }>('/context/search', {
        query: searchQuery,
        top_k: 8,
      });
      setSearchResults(res.results || []);
    } catch { /* ignore */ } finally {
      setIsSearching(false);
    }
  }, [searchQuery]);

  const handleDelete = useCallback(async (docId: string) => {
    try {
      await apiDelete(`/context/documents/${docId}`);
      setDocuments(prev => prev.filter(d => d.id !== docId));
    } catch { /* ignore */ }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    handleFileUpload(e.dataTransfer.files);
  }, [handleFileUpload]);

  return (
    <div className="space-y-3">
      {/* Header Stats */}
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-lg p-2.5 text-center">
          <Database className="w-3.5 h-3.5 mx-auto mb-1 text-deloitte-green" />
          <div className="text-sm font-bold text-white">{summary.total_documents || documents.length}</div>
          <div className="text-[8px] text-surface-500 uppercase tracking-wider">Documents</div>
        </div>
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-lg p-2.5 text-center">
          <FolderOpen className="w-3.5 h-3.5 mx-auto mb-1 text-sky-400" />
          <div className="text-sm font-bold text-white">{summary.total_chunks || 0}</div>
          <div className="text-[8px] text-surface-500 uppercase tracking-wider">Chunks</div>
        </div>
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-lg p-2.5 text-center">
          <FileText className="w-3.5 h-3.5 mx-auto mb-1 text-amber-400" />
          <div className="text-sm font-bold text-white">{formatBytes(summary.total_size_bytes || 0)}</div>
          <div className="text-[8px] text-surface-500 uppercase tracking-wider">Total Size</div>
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex gap-1 bg-surface-800/40 rounded-lg p-0.5">
        {(['documents', 'upload', 'search'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`flex-1 py-1.5 px-2 rounded-md text-[10px] font-semibold uppercase tracking-wider transition-all ${
              activeTab === tab
                ? 'bg-deloitte-green/20 text-deloitte-green'
                : 'text-surface-500 hover:text-surface-300'
            }`}
          >
            {tab === 'documents' && <FolderOpen className="w-3 h-3 inline mr-1" />}
            {tab === 'upload' && <Upload className="w-3 h-3 inline mr-1" />}
            {tab === 'search' && <Search className="w-3 h-3 inline mr-1" />}
            {tab}
          </button>
        ))}
      </div>

      {/* Upload Tab */}
      {activeTab === 'upload' && (
        <div className="space-y-3">
          {/* File Upload Zone */}
          <div
            onDrop={handleDrop}
            onDragOver={e => e.preventDefault()}
            onClick={() => fileInputRef.current?.click()}
            className="border-2 border-dashed border-surface-600 hover:border-deloitte-green/50 rounded-xl p-6 text-center cursor-pointer transition-colors"
          >
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.docx,.doc,.pptx,.xlsx,.xls,.csv,.txt,.md,.html,.htm"
              className="hidden"
              onChange={e => handleFileUpload(e.target.files)}
            />
            {isUploading ? (
              <Loader2 className="w-8 h-8 mx-auto mb-2 text-deloitte-green animate-spin" />
            ) : (
              <Upload className="w-8 h-8 mx-auto mb-2 text-surface-500" />
            )}
            <p className="text-xs text-surface-400 mb-1">
              {isUploading ? 'Processing...' : 'Drop files here or click to upload'}
            </p>
            <p className="text-[10px] text-surface-600">
              PDF, DOCX, PPTX, XLSX, CSV, TXT, HTML
            </p>
          </div>

          {/* URL Ingestion */}
          <div className="bg-surface-800/40 border border-surface-700/50 rounded-lg p-3">
            <div className="flex items-center gap-2 mb-2">
              <Link2 className="w-3.5 h-3.5 text-sky-400" />
              <span className="text-[10px] text-surface-400 uppercase tracking-wider font-semibold">Index from URL</span>
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={urlInput}
                onChange={e => setUrlInput(e.target.value)}
                placeholder="https://example.com/report"
                className="flex-1 bg-surface-900/60 border border-surface-700 rounded-lg px-3 py-1.5 text-xs text-white placeholder-surface-600 focus:outline-none focus:border-deloitte-green/50"
                onKeyDown={e => e.key === 'Enter' && handleUrlIngest()}
              />
              <button
                onClick={handleUrlIngest}
                disabled={isIngesting || !urlInput.trim()}
                className="px-3 py-1.5 bg-sky-500/20 hover:bg-sky-500/30 text-sky-400 rounded-lg text-xs font-medium disabled:opacity-40 transition-colors flex items-center gap-1"
              >
                {isIngesting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                Index
              </button>
            </div>
          </div>

          {/* Status Messages */}
          {uploadSuccess && (
            <div className="flex items-center gap-2 bg-deloitte-green/10 border border-deloitte-green/30 rounded-lg px-3 py-2 text-xs text-deloitte-green">
              <CheckCircle className="w-3.5 h-3.5" /> {uploadSuccess}
            </div>
          )}
          {uploadError && (
            <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2 text-xs text-red-400">
              <XCircle className="w-3.5 h-3.5" /> {uploadError}
            </div>
          )}
        </div>
      )}

      {/* Documents Tab */}
      {activeTab === 'documents' && (
        <div className="space-y-2">
          {/* Filters */}
          <div className="flex gap-2">
            <select
              value={scopeFilter}
              onChange={e => setScopeFilter(e.target.value)}
              className="bg-surface-800/60 border border-surface-700/50 rounded-lg px-2 py-1 text-[10px] text-surface-300 focus:outline-none"
            >
              <option value="all">All Scopes</option>
              <option value="user">My Documents</option>
              <option value="conversation">This Chat</option>
              <option value="global">Global</option>
            </select>
            {uniqueTypes.length > 1 && (
              <select
                value={typeFilter}
                onChange={e => setTypeFilter(e.target.value)}
                className="bg-surface-800/60 border border-surface-700/50 rounded-lg px-2 py-1 text-[10px] text-surface-300 focus:outline-none"
              >
                <option value="all">All Types</option>
                {uniqueTypes.map(t => (
                  <option key={t} value={t}>{t.toUpperCase()}</option>
                ))}
              </select>
            )}
          </div>

          {/* Document List */}
          {filteredDocs.length === 0 ? (
            <div className="text-center py-8 text-surface-600">
              <FolderOpen className="w-8 h-8 mx-auto mb-2 opacity-40" />
              <p className="text-xs">No documents uploaded yet</p>
              <button
                onClick={() => setActiveTab('upload')}
                className="mt-2 text-[10px] text-deloitte-green hover:underline"
              >
                Upload your first document
              </button>
            </div>
          ) : (
            <div className="space-y-1.5">
              {filteredDocs.map(doc => {
                const Icon = FILE_ICONS[doc.file_type] || File;
                const isExpanded = expandedDoc === doc.id;
                return (
                  <div
                    key={doc.id}
                    className="bg-surface-800/40 border border-surface-700/50 rounded-lg overflow-hidden"
                  >
                    <div
                      className="flex items-center gap-2 p-2.5 cursor-pointer hover:bg-surface-700/30 transition-colors"
                      onClick={() => setExpandedDoc(isExpanded ? null : doc.id)}
                    >
                      <Icon className="w-4 h-4 text-surface-400 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs text-white font-medium truncate">
                          {doc.original_name}
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className={`text-[8px] px-1.5 py-0.5 rounded border ${STATUS_STYLES[doc.status] || ''}`}>
                            {doc.status}
                          </span>
                          <span className="text-[8px] text-surface-600">
                            {doc.chunk_count} chunks
                          </span>
                          <span className="text-[8px] text-surface-600">
                            {formatBytes(doc.file_size_bytes)}
                          </span>
                        </div>
                      </div>
                      <button
                        onClick={e => { e.stopPropagation(); handleDelete(doc.id); }}
                        className="p-1 text-surface-600 hover:text-red-400 transition-colors"
                        title="Delete"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                      {isExpanded ? (
                        <ChevronUp className="w-3 h-3 text-surface-500" />
                      ) : (
                        <ChevronDown className="w-3 h-3 text-surface-500" />
                      )}
                    </div>
                    {isExpanded && (
                      <div className="px-3 pb-2.5 border-t border-surface-700/30 pt-2 space-y-1.5">
                        {doc.description && (
                          <p className="text-[10px] text-surface-400 italic">{doc.description}</p>
                        )}
                        <div className="flex flex-wrap gap-1.5 text-[9px] text-surface-500">
                          <span className="flex items-center gap-0.5">
                            <Tag className="w-2.5 h-2.5" /> {doc.file_type.toUpperCase()}
                          </span>
                          <span className="flex items-center gap-0.5">
                            <Clock className="w-2.5 h-2.5" /> {formatDate(doc.created_at)}
                          </span>
                          <span className="px-1.5 py-0.5 bg-surface-700/50 rounded">
                            {SCOPE_LABELS[doc.scope] || doc.scope}
                          </span>
                        </div>
                        {doc.source_url && (
                          <a
                            href={doc.source_url}
                            target="_blank"
                            rel="noreferrer"
                            className="flex items-center gap-1 text-[10px] text-sky-400 hover:underline"
                          >
                            <ExternalLink className="w-2.5 h-2.5" /> {doc.source_url}
                          </a>
                        )}
                        {doc.tags && doc.tags.length > 0 && (
                          <div className="flex gap-1">
                            {doc.tags.map((tag, i) => (
                              <span
                                key={i}
                                className="text-[8px] px-1.5 py-0.5 bg-deloitte-green/10 text-deloitte-green rounded"
                              >
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Search Tab */}
      {activeTab === 'search' && (
        <div className="space-y-3">
          <div className="flex gap-2">
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Search your documents..."
              className="flex-1 bg-surface-900/60 border border-surface-700 rounded-lg px-3 py-2 text-xs text-white placeholder-surface-600 focus:outline-none focus:border-deloitte-green/50"
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
            />
            <button
              onClick={handleSearch}
              disabled={isSearching || !searchQuery.trim()}
              className="px-3 py-2 bg-deloitte-green/20 hover:bg-deloitte-green/30 text-deloitte-green rounded-lg text-xs font-medium disabled:opacity-40 transition-colors flex items-center gap-1"
            >
              {isSearching ? <Loader2 className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3" />}
              Search
            </button>
          </div>

          {searchResults.length > 0 ? (
            <div className="space-y-2">
              <p className="text-[10px] text-surface-500">{searchResults.length} results found</p>
              {searchResults.map((result, i) => (
                <div
                  key={result.chunk_id}
                  className="bg-surface-800/40 border border-surface-700/50 rounded-lg p-2.5"
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="text-[10px] font-bold text-deloitte-green">{i + 1}</span>
                      <span className="text-[10px] text-white font-medium truncate max-w-[200px]">
                        {result.original_name}
                      </span>
                    </div>
                    <span className="text-[9px] px-1.5 py-0.5 bg-deloitte-green/10 text-deloitte-green rounded">
                      {Math.round(result.score * 100)}% match
                    </span>
                  </div>
                  <p className="text-[10px] text-surface-400 leading-relaxed line-clamp-4">
                    {result.content}
                  </p>
                </div>
              ))}
            </div>
          ) : searchQuery && !isSearching ? (
            <div className="text-center py-6 text-surface-600">
              <Search className="w-6 h-6 mx-auto mb-2 opacity-40" />
              <p className="text-xs">No results found</p>
              <p className="text-[10px] mt-1">Try different keywords or upload more documents</p>
            </div>
          ) : !searchQuery ? (
            <div className="text-center py-6 text-surface-600">
              <Search className="w-6 h-6 mx-auto mb-2 opacity-40" />
              <p className="text-xs">Search across all your uploaded documents</p>
              <p className="text-[10px] mt-1">Uses semantic search to find relevant content</p>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
