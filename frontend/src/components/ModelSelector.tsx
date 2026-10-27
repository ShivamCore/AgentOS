"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useAppStore } from "@/lib/store";
import { ChevronDown, Check, Bot, RefreshCw } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

export function ModelSelector() {
  const models        = useAppStore((s) => s.models);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const setSelected   = useAppStore((s) => s.setSelectedModel);
  const fetchModels   = useAppStore((s) => s.fetchModels);
  const status        = useAppStore((s) => s.status);

  const [open, setOpen]       = useState(false);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  // Auto-refresh every 60s so newly pulled models appear
  useEffect(() => {
    const interval = setInterval(() => fetchModels(), 60_000);
    return () => clearInterval(interval);
  }, [fetchModels]);

  const refresh = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    setLoading(true);
    await fetchModels();
    setLoading(false);
  }, [fetchModels]);

  const disabled = status === "running" || status === "pending";

  // Split: "Auto" stays first, then real Ollama models sorted
  const autoEntry   = models.filter((m) => m === "Auto");
  const ollamaModels = models.filter((m) => m !== "Auto").sort();
  const sorted = [...autoEntry, ...ollamaModels];

  return (
    <div ref={ref} className="relative">
      {/* Trigger button */}
      <button
        onClick={() => !disabled && setOpen((o) => !o)}
        disabled={disabled}
        className={`flex items-center gap-2 h-8 px-3 rounded-lg border text-[11px] font-mono transition-colors
          ${disabled
            ? "border-white/5 text-slate-700 cursor-not-allowed"
            : "border-white/10 bg-white/[0.03] text-slate-400 hover:text-slate-200 hover:border-white/20 cursor-pointer"
          }`}
      >
        <Bot className="w-3.5 h-3.5 text-blue-400/60 shrink-0" />
        <span className="max-w-[140px] truncate">{selectedModel}</span>
        <ChevronDown className={`w-3.5 h-3.5 transition-transform duration-200 shrink-0 ${open ? "rotate-180" : ""}`} />
      </button>

      {/* Dropdown */}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.12 }}
            className="absolute right-0 top-full mt-1.5 w-64 rounded-xl border border-white/10 bg-[#0d1117] shadow-2xl z-50 overflow-hidden"
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2 border-b border-white/[0.06] bg-[#161b22]">
              <span className="text-[10px] font-mono text-white/30 uppercase tracking-widest">Ollama models</span>
              <button
                onClick={refresh}
                className="text-white/30 hover:text-white/60 transition-colors"
                title="Refresh model list"
              >
                <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
              </button>
            </div>

            {/* Model list */}
            <div className="py-1 max-h-64 overflow-y-auto">
              {sorted.length === 0 ? (
                <p className="px-4 py-3 text-[11px] text-white/20 font-mono">
                  No models found. Is Ollama running?
                </p>
              ) : (
                sorted.map((m) => (
                  <button
                    key={m}
                    onClick={() => { setSelected(m); setOpen(false); }}
                    className={`w-full flex items-center justify-between gap-2 px-4 py-2.5 text-[12px] font-mono transition-colors text-left
                      ${m === selectedModel
                        ? "text-blue-400 bg-blue-950/30"
                        : "text-slate-400 hover:text-slate-200 hover:bg-white/[0.03]"
                      }`}
                  >
                    <span className="truncate">{m}</span>
                    {m === "Auto" && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-blue-950/40 text-blue-400/60 border border-blue-800/30 shrink-0">
                        smart
                      </span>
                    )}
                    {m === selectedModel && m !== "Auto" && <Check className="w-3.5 h-3.5 shrink-0" />}
                  </button>
                ))
              )}
            </div>

            {/* Footer hint */}
            <div className="px-4 py-1.5 border-t border-white/[0.06] bg-[#161b22]">
              <p className="text-[10px] font-mono text-white/20">
                {ollamaModels.length} model{ollamaModels.length !== 1 ? "s" : ""} installed · auto-refreshes every 60s
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
