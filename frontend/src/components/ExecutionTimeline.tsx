import { useAppStore } from "@/lib/store";

export function ExecutionTimeline() {
  const nodes = useAppStore((state) => state.nodes);

  return (
    <div className="flex-1 flex flex-col min-h-0 relative bg-[#0b0f17]/50 backdrop-blur-sm">
      <div className="px-6 py-4 border-b border-white/5 bg-[#111827]/80 backdrop-blur-md sticky top-0 z-10 flex justify-between items-center">
        <h3 className="text-xs font-mono uppercase tracking-widest text-gray-400 font-semibold">Execution Timeline</h3>
        <span className="text-[10px] text-gray-500 font-mono">{nodes.length} Steps</span>
      </div>
      
      <div className="flex-1 overflow-y-scroll p-6 min-h-0 relative scroll-smooth">
        {nodes.length === 0 ? (
          <div className="text-gray-600 italic">No execution steps yet.</div>
        ) : (
          <div className="relative border-l border-white/10 ml-3 space-y-8 pb-4">
            {nodes.map((node) => (
              <div key={node.node_id} className="relative pl-6">
                <div className="absolute -left-[5px] top-1 w-2.5 h-2.5 rounded-full bg-[#0b0f17] border-[2px] z-10" style={{ borderColor: node.status === 'completed' ? '#22c55e' : node.status === 'running' ? '#3b82f6' : node.status === 'failed' ? '#ef4444' : '#4b5563' }} />
                
                {node.status === 'running' && (
                  <div className="absolute -left-[9px] top-0 w-4 h-4 rounded-full bg-blue-500/20 animate-ping" />
                )}

                <div className="flex flex-col gap-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-mono font-bold tracking-widest text-gray-300">[{node.node_id}]</span>
                    <span className={`text-[10px] font-mono tracking-widest uppercase ${node.status === 'completed' ? 'text-green-500' : node.status === 'running' ? 'text-blue-500' : node.status === 'failed' ? 'text-red-500' : 'text-gray-500'}`}>
                      {node.status}
                    </span>
                  </div>
                  <p className="text-sm text-gray-400 mt-1 leading-relaxed">{node.description}</p>
                  
                  {node.files_modified > 0 && (
                    <div className="mt-2 text-[10px] font-mono text-purple-400 bg-purple-500/10 border border-purple-500/20 rounded px-2 py-1 w-max">
                      {node.files_modified} files modified
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
