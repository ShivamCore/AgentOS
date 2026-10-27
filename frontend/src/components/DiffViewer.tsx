"use client";

import { useState } from "react";
import { useAppStore } from "@/lib/store";
import { FileExplorer } from "@/components/FileExplorer";
import { motion, AnimatePresence } from "framer-motion";
import { FileDiff, Eye, Files } from "lucide-react";

// ── Inline syntax coloring (no extra lib needed for basic readability) ─────────
function colorize(line: string): { text: string; cls: string } {
  if (line.startsWith("+")) return { text: line, cls: "text-emerald-400 bg-emerald-950/20" };
  if (line.startsWith("-")) return { text: line, cls: "text-rose-400 bg-rose-950/20" };
  if (line.startsWith("@@")) return { text: line, cls: "text-cyan-400/70 bg-cyan-950/10" };
  return { text: line, cls: "text-slate-400" };
}

// ── Build a character-level diff between two strings ─────────────────────────
function buildLineDiff(prev: string, next: string): string {
  const prevLines = (prev || "").split("\n");
  const nextLines = (next || "").split("\n");
  const output: string[] = [];

  const maxLen = Math.max(prevLines.length, nextLines.length);
  for (let i = 0; i < maxLen; i++) {
    const p = prevLines[i];
    const n = nextLines[i];
    if (p === undefined) {
      output.push(`+ ${n}`);
    } else if (n === undefined) {
      output.push(`- ${p}`);
    } else if (p !== n) {
      output.push(`- ${p}`);
      output.push(`+ ${n}`);
    } else {
      output.push(`  ${p}`);
    }
  }
  return output.join("\n");
}

// ── Code view ─────────────────────────────────────────────────────────────────
function CodeView({ content }: { content: string }) {
  return (
    <div
      className="flex-1 overflow-auto min-h-0 font-mono text-[12px] leading-relaxed"
      style={{ scrollbarWidth: "thin", scrollbarColor: "#1e293b transparent" }}
    >
      {content.split("\n").map((line, i) => (
        <div key={i} className="flex">
          <span className="select-none w-10 shrink-0 text-right text-slate-700 pr-3 border-r border-white/[0.04]">
            {i + 1}
          </span>
          <span className="pl-3 text-slate-300 whitespace-pre break-all">{line || " "}</span>
        </div>
      ))}
    </div>
  );
}

// ── Diff view ─────────────────────────────────────────────────────────────────
function DiffView({ previousContent, content }: { previousContent: string; content: string }) {
  const diff = buildLineDiff(previousContent, content);

  return (
    <div
      className="flex-1 overflow-auto min-h-0 font-mono text-[12px] leading-relaxed"
      style={{ scrollbarWidth: "thin", scrollbarColor: "#1e293b transparent" }}
    >
      {diff.split("\n").map((line, i) => {
        const { text, cls } = colorize(line);
        return (
          <div key={i} className={`flex ${cls} px-0`}>
            <span className="select-none w-10 shrink-0 text-right text-slate-700 pr-3 border-r border-white/[0.04]">
              {i + 1}
            </span>
            <span className="pl-3 whitespace-pre break-all">{text || " "}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── DiffViewer ────────────────────────────────────────────────────────────────
export function DiffViewer() {
  const fileEdits = useAppStore((s) => s.fileEdits);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"diff" | "code">("diff");

  // Get the selected edit and its predecessor
  const selectedEdit = fileEdits.find((f) => f.file_path === selectedPath);
  const allForPath = fileEdits.filter((f) => f.file_path === selectedPath);
  const prevEdit = allForPath.length > 1 ? allForPath[allForPath.length - 2] : null;

  const handleSelect = (path: string, _content: string) => {
    setSelectedPath(path);
  };

  return (
    <div className="h-full flex flex-col bg-[#080c14]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/5 bg-[#0a0e18] shrink-0">
        <div className="flex items-center gap-2">
          <Files className="w-3.5 h-3.5 text-slate-600" />
          <span className="text-[10px] font-mono uppercase tracking-widest text-slate-500">
            File System
          </span>
          {selectedPath && (
            <span className="text-[10px] text-slate-600 font-mono ml-1 max-w-[180px] truncate">
              / {selectedPath}
            </span>
          )}
        </div>

        {selectedEdit && (
          <div className="flex items-center gap-1">
            <button
              onClick={() => setViewMode("diff")}
              className={`flex items-center gap-1 h-6 px-2 rounded text-[10px] border transition-colors ${
                viewMode === "diff"
                  ? "bg-blue-950/40 border-blue-500/30 text-blue-400"
                  : "bg-white/5 border-white/10 text-slate-500 hover:text-slate-300"
              }`}
            >
              <FileDiff className="w-3 h-3" />
              Diff
            </button>
            <button
              onClick={() => setViewMode("code")}
              className={`flex items-center gap-1 h-6 px-2 rounded text-[10px] border transition-colors ${
                viewMode === "code"
                  ? "bg-blue-950/40 border-blue-500/30 text-blue-400"
                  : "bg-white/5 border-white/10 text-slate-500 hover:text-slate-300"
              }`}
            >
              <Eye className="w-3 h-3" />
              Code
            </button>
          </div>
        )}
      </div>

      {/* Body: file tree + code/diff pane */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* File Tree */}
        <div className="w-44 shrink-0 border-r border-white/5 bg-[#080c14] overflow-hidden">
          <FileExplorer selected={selectedPath} onSelect={handleSelect} />
        </div>

        {/* Content pane */}
        <div className="flex-1 min-w-0 flex flex-col bg-[#070a11] overflow-hidden">
          <AnimatePresence mode="wait">
            {!selectedEdit ? (
              <motion.div
                key="empty"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex-1 flex flex-col items-center justify-center gap-2 text-slate-700"
              >
                <FileDiff className="w-6 h-6 text-slate-800" />
                <span className="text-[11px] font-mono uppercase tracking-widest">
                  Select a file
                </span>
              </motion.div>
            ) : (
              <motion.div
                key={selectedPath + viewMode}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="flex flex-col flex-1 min-h-0 overflow-hidden"
              >
                {viewMode === "diff" && prevEdit ? (
                  <DiffView previousContent={prevEdit.content} content={selectedEdit.content} />
                ) : viewMode === "diff" && !prevEdit ? (
                  // No previous — show as all-new diff
                  <DiffView previousContent="" content={selectedEdit.content} />
                ) : (
                  <CodeView content={selectedEdit.content} />
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
