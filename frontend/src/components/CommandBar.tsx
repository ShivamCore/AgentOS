"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useAppStore } from "@/lib/store";
import { motion, AnimatePresence } from "framer-motion";
import { ModelSelector } from "@/components/ModelSelector";
import { Zap, Square } from "lucide-react";

const PLACEHOLDERS = [
  "Build a FastAPI CRUD service with PostgreSQL…",
  "Write unit tests for the auth module…",
  "Refactor the worker pool for async execution…",
  "Create a React dashboard with live charts…",
  "Debug the memory leak in the event loop…",
];

export function CommandBar() {
  const [input, setInput] = useState("");
  const [placeholder, setPlaceholder] = useState(PLACEHOLDERS[0]);
  const [focused, setFocused] = useState(false);
  const startTask = useAppStore((s) => s.startTask);
  const stopWebSocket = useAppStore((s) => s.stopWebSocket);
  const status = useAppStore((s) => s.status);
  const taskId = useAppStore((s) => s.taskId);
  const inputRef = useRef<HTMLInputElement>(null);
  const phIndex = useRef(0);

  // Rotate placeholder every 4 seconds
  useEffect(() => {
    const id = setInterval(() => {
      phIndex.current = (phIndex.current + 1) % PLACEHOLDERS.length;
      setPlaceholder(PLACEHOLDERS[phIndex.current]);
    }, 4000);
    return () => clearInterval(id);
  }, []);

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (input.trim() && status !== "running" && status !== "pending") {
        startTask(input.trim());
        setInput("");
      }
    },
    [input, status, startTask]
  );

  const isActive = status === "running" || status === "pending";

  return (
    <div className="w-full relative z-30 shrink-0 border-b border-white/[0.05] bg-[#060a10]">
      {/* Top accent line */}
      <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-transparent via-blue-500/20 to-transparent" />

      <div className="flex items-center gap-3 px-4 py-3">
        {/* Logo mark */}
        <div className="flex items-center gap-2 shrink-0">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500/20 to-violet-500/20 border border-blue-500/20 flex items-center justify-center">
            <Zap className="w-3.5 h-3.5 text-blue-400" />
          </div>
          <span className="text-[11px] font-mono uppercase tracking-widest text-slate-500 hidden sm:block">
            AgentOS
          </span>
        </div>

        {/* Separator */}
        <div className="w-px h-5 bg-white/[0.06] shrink-0" />

        {/* Input */}
        <form onSubmit={handleSubmit} className="flex-1 min-w-0 relative group">
          {/* Glow */}
          <div
            className={`absolute -inset-0.5 rounded-xl blur-sm transition-opacity duration-500 pointer-events-none
              ${focused ? "opacity-100 bg-gradient-to-r from-blue-500/20 via-violet-500/20 to-blue-500/20" : "opacity-0"}`}
          />

          <div className="relative flex items-center">
            {/* Prompt symbol */}
            <span className="absolute left-3.5 text-slate-600 font-mono text-sm select-none pointer-events-none">
              ❯
            </span>
            <input
              ref={inputRef}
              autoFocus
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              placeholder={focused ? "" : placeholder}
              disabled={isActive}
              className="w-full relative z-10 bg-[#0d1117] border border-white/[0.07] rounded-xl py-3 pl-9 pr-4
                text-[14px] font-mono text-slate-200 placeholder-slate-700
                focus:outline-none focus:border-blue-500/40 transition-all duration-200
                disabled:opacity-40 disabled:cursor-not-allowed shadow-xl"
            />
          </div>
        </form>

        {/* Status / Stop */}
        <AnimatePresence mode="wait">
          {isActive ? (
            <motion.div
              key="active"
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              className="flex items-center gap-2 shrink-0"
            >
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-blue-950/40 border border-blue-500/20">
                <motion.div
                  animate={{ opacity: [0.4, 1, 0.4] }}
                  transition={{ repeat: Infinity, duration: 1.2 }}
                  className="w-1.5 h-1.5 rounded-full bg-blue-400"
                />
                <span className="text-[10px] font-mono uppercase tracking-widest text-blue-400">
                  {status}
                </span>
              </div>
              <button
                onClick={() => stopWebSocket()}
                className="p-2 rounded-lg bg-rose-950/30 border border-rose-500/20 text-rose-400 hover:bg-rose-950/50 transition-colors"
                title="Stop execution"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
              </button>
            </motion.div>
          ) : (
            <motion.div
              key="idle"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="shrink-0"
            >
              <ModelSelector />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
