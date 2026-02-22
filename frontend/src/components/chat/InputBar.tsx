import { useState, useRef, useCallback } from 'react';
import { Send, Paperclip, Loader2, FileText, CheckCircle } from 'lucide-react';
import { uploadFile, apiPost } from '../../api/client';

const ACTUALS_EXTENSIONS = new Set(['.csv', '.xlsx', '.xls']);
const CONTEXT_EXTENSIONS = new Set(['.pdf', '.docx', '.doc', '.pptx', '.txt', '.md', '.html', '.htm']);

function getFileExtension(name: string): string {
  return (name.lastIndexOf('.') >= 0 ? name.slice(name.lastIndexOf('.')) : '').toLowerCase();
}

interface Props {
  onSend: (content: string) => void;
  isStreaming: boolean;
}

export function InputBar({ onSend, isStreaming }: Props) {
  const [input, setInput] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    onSend(trimmed);
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
  }, [input, isStreaming, onSend]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsUploading(true);
    setUploadStatus(null);

    const ext = getFileExtension(file.name);

    try {
      if (ACTUALS_EXTENSIONS.has(ext)) {
        const result = await uploadFile('/upload/actuals', file);
        onSend(`I've uploaded a file: ${result.filename}. Please ingest the actuals data from ${result.file_path}`);
      } else if (CONTEXT_EXTENSIONS.has(ext) || !ACTUALS_EXTENSIONS.has(ext)) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('scope', 'user');
        const result = await apiPost<any>('/context/upload', formData);
        if (result.status === 'ready') {
          setUploadStatus(`${file.name} indexed (${result.chunk_count} chunks)`);
          onSend(`I've uploaded "${file.name}" to the document library. It has been indexed with ${result.chunk_count} chunks. You can now search it for relevant context.`);
        } else if (result.status === 'failed') {
          onSend(`Upload processing failed for ${file.name}: ${result.error_message || 'Unknown error'}`);
        } else {
          setUploadStatus(`${file.name} is being processed...`);
          onSend(`I've uploaded "${file.name}" to the document library. It's being processed for indexing.`);
        }
        setTimeout(() => setUploadStatus(null), 5000);
      }
    } catch (error: any) {
      onSend(`Upload failed: ${error.message}`);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const textarea = e.target;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  };

  return (
    <div className="relative">
      {uploadStatus && (
        <div className="absolute -top-8 left-0 right-0 flex items-center justify-center gap-1.5 text-[10px] text-deloitte-green animate-fade-in">
          <CheckCircle className="w-3 h-3" /> {uploadStatus}
        </div>
      )}
      <div className="flex items-end gap-2 bg-surface-800 border border-surface-700 rounded-2xl px-4 py-3 focus-within:border-deloitte-green/40 focus-within:glow-green transition-all">
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={isUploading}
          className="flex-shrink-0 p-1.5 hover:bg-surface-700 rounded-lg transition-colors text-surface-400 hover:text-deloitte-green disabled:opacity-50"
          title="Upload file (CSV/Excel for actuals, PDF/DOCX/PPTX/TXT for context)"
        >
          {isUploading ? (
            <Loader2 className="w-5 h-5 animate-spin" />
          ) : (
            <Paperclip className="w-5 h-5" />
          )}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.xlsx,.xls,.pdf,.docx,.doc,.pptx,.txt,.md,.html,.htm"
          onChange={handleFileUpload}
          className="hidden"
        />

        <textarea
          ref={textareaRef}
          value={input}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your forecast..."
          rows={1}
          className="flex-1 bg-transparent text-white placeholder-surface-500 resize-none focus:outline-none text-sm leading-6 max-h-[200px]"
          disabled={isStreaming}
        />

        <button
          onClick={handleSend}
          disabled={!input.trim() || isStreaming}
          className="flex-shrink-0 p-1.5 bg-deloitte-green hover:bg-deloitte-green/90 disabled:bg-surface-700 disabled:text-surface-600 text-black rounded-lg transition-colors"
        >
          {isStreaming ? (
            <Loader2 className="w-5 h-5 animate-spin text-deloitte-green" />
          ) : (
            <Send className="w-5 h-5" />
          )}
        </button>
      </div>

      <p className="text-center text-xs text-surface-500 mt-2">
        AI-generated forecasts require human review before publication
      </p>
    </div>
  );
}
