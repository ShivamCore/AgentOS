"use client";

import { useAppStore } from "@/lib/store";
import { useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { SystemControl } from "@/components/SystemControl";
import { ModelSelector } from "@/components/ModelSelector";

export function StatusBar() {
  const status = useAppStore((state) => state.status);
  const workers = useAppStore((state) => state.workers);
  const pollState = useAppStore((state) => state.pollState);
  const nodes = useAppStore((state) => state.nodes);

  const activeAgents = nodes.filter(n => n.status === "running").map(n => {
    if (n.node_id === "plan") return "Planner";
    if (n.node_id.includes("test")) return "Tester";
    if (n.node_id.includes("debug")) return "Debugger";
    return "Coder";
  });

  useEffect(() => {
    pollState();
    // Native WebSocket Streaming active: no loop polling.
  }, [pollState]);

  return (
    <div className="w-full flex items-center justify-between px-6 py-3 border-t border-white/5 bg-[#0b0f17]/90 backdrop-blur-md text-[10px] font-mono text-gray-400 shrink-0 uppercase tracking-widest">
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2">
          {status === "running" ? (
            <motion.div animate={{ opacity: [0.3, 1, 0.3] }} transition={{ repeat: Infinity, duration: 1 }}>
              <div className="w-2 h-2 rounded-full bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.8)]" />
            </motion.div>
          ) : (
            <div className="w-2 h-2 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.4)]" />
          )}
          
          <AnimatePresence mode="popLayout">
            <motion.span 
              key={status}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="min-w-[150px]"
            >
              ● SYSTEM: <span className="text-gray-300 font-semibold">{status.toUpperCase()}</span>
            </motion.span>
          </AnimatePresence>
        </div>

        <div className="hidden sm:flex items-center border-l border-white/10 pl-6 gap-6">
          <div className="flex items-center gap-2">
            <span>● QUEUE:</span>
            <span className="text-purple-400 font-semibold">{status === "pending" || status === "running" ? "ACTIVE" : "EMPTY"}</span>
          </div>
          <div className="flex items-center gap-2 border-l border-white/10 pl-6">
            <span>● SUB-AGENTS:</span>
            <div className="flex items-center gap-2 ml-1">
              <span className={`px-1.5 py-0.5 rounded text-[9px] ${activeAgents.includes("Planner") ? "bg-blue-500/20 text-blue-400" : "bg-white/5 text-gray-600"}`}>PLANNER</span>
              <span className={`px-1.5 py-0.5 rounded text-[9px] ${activeAgents.includes("Coder") ? "bg-green-500/20 text-green-400" : "bg-white/5 text-gray-600"}`}>CODER</span>
              <span className={`px-1.5 py-0.5 rounded text-[9px] ${activeAgents.includes("Tester") ? "bg-yellow-500/20 text-yellow-400" : "bg-white/5 text-gray-600"}`}>TESTER</span>
              <span className={`px-1.5 py-0.5 rounded text-[9px] ${activeAgents.includes("Debugger") ? "bg-red-500/20 text-red-400" : "bg-white/5 text-gray-600"}`}>DEBUGGER</span>
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2 border-r border-white/10 pr-6">
          <span>● WORKERS: <span className="text-blue-400 font-semibold">{workers} ACTIVE</span></span>
        </div>
        <div className="border-r border-white/10 pr-6">
          <ModelSelector />
        </div>
        <SystemControl />
      </div>
    </div>
  );
}
