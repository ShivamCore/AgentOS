import { useAppStore } from "@/lib/store";
import { FileCode, Activity } from "lucide-react";

export function FileDiffViewer() {
  const fileEdits = useAppStore((state) => state.fileEdits);

  return (
    <div className="flex-1 flex flex-col min-h-0 relative h-full">
      <div className="px-4 py-3 border-b border-white/5 bg-[#111827]/80 backdrop-blur-md sticky top-0 z-10 flex justify-between items-center">
        <h3 className="text-xs font-mono uppercase tracking-widest text-gray-400 font-semibold flex items-center gap-2">
          <FileCode className="w-3 h-3 text-purple-400" /> System Files Tracker
        </h3>
        <span className="text-[10px] text-gray-500 font-mono">{fileEdits.length} Edits</span>
      </div>
      
      <div className="flex-1 overflow-y-scroll p-4 min-h-0 relative scroll-smooth space-y-4">
        {fileEdits.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-gray-600 italic text-sm gap-2">
            <Activity className="w-6 h-6 opacity-30" />
            No files modified yet.
          </div>
        ) : (
          fileEdits.map((edit) => (
            <div key={edit.id} className="w-full flex flex-col bg-[#0b0f17] border border-white/5 rounded-lg overflow-hidden">
              <div className="px-3 py-2 bg-white/5 border-b border-white/5 text-xs font-mono text-blue-300 break-all">
                {edit.file_path}
                <span className="text-[10px] text-gray-500 ml-2">({edit.node_id})</span>
              </div>
              <div className="p-3 bg-[#0b0f17]">
                <pre className="text-[10px] font-mono text-gray-400 whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-scroll scrollbar-none">
                  {edit.content || "Empty content or binary file."}
                </pre>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
