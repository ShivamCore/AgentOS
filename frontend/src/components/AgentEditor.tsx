"use client";

import { useAppStore, AgentStatus } from "@/lib/store";
import { useEffect, useState, useRef } from "react";
import MonacoEditor, { useMonaco, Monaco } from "@monaco-editor/react";
import { motion } from "framer-motion";
import { Terminal, Save, CheckCircle2, AlertTriangle, XCircle, Search, Sparkles } from "lucide-react";

export function AgentEditor() {
  const agentStatuses = useAppStore((s) => s.agentStatuses);
  const pollState = useAppStore((s) => s.pollState);
  
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [isSaving, setIsSaving] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  
  const monaco = useMonaco();
  const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  // CHANGED: Item 12 - Monaco Auto-Complete Schema Hooks dynamically injected.
  useEffect(() => {
    if (monaco) {
      monaco.languages.registerCompletionItemProvider("markdown", {
        provideCompletionItems: (model, position) => {
          const word = model.getWordUntilPosition(position);
          const range = {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: word.startColumn,
            endColumn: word.endColumn
          };
          const suggestions = [
            { label: "capabilities", kind: monaco.languages.CompletionItemKind.Keyword, insertText: "## Capabilities\n- ", range },
            { label: "constraints", kind: monaco.languages.CompletionItemKind.Keyword, insertText: "## Constraints\n- ", range },
            { label: "json_output", kind: monaco.languages.CompletionItemKind.Value, insertText: "json_output: true", range },
            { label: "max_input_tokens", kind: monaco.languages.CompletionItemKind.Property, insertText: "max_input_tokens: 8000", range },
            { label: "max_output_tokens", kind: monaco.languages.CompletionItemKind.Property, insertText: "max_output_tokens: 4000", range },
            { label: "schema_version", kind: monaco.languages.CompletionItemKind.Property, insertText: "schema_version: 1", range },
            { label: "fallback_to", kind: monaco.languages.CompletionItemKind.Property, insertText: "fallback_to: planner", range }
          ];
          return { suggestions };
        }
      });
    }
  }, [monaco]);

  useEffect(() => {
    if (selectedAgent) {
      fetch(`${apiBase}/agents/${selectedAgent}/content`)
        .then(r => r.json())
        .then(d => {
            setContent(d.content || "");
            setIsDirty(false);
        });
    }
  }, [selectedAgent, apiBase]);

  const handleSave = async () => {
    if (!selectedAgent) return;
    setIsSaving(true);
    try {
      await fetch(`${apiBase}/agents/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: selectedAgent, content })
      });
      // CHANGED: Item 11 - Immediate Re-poll of Agent Config state post-save to validate
      await pollState();
      setIsDirty(false);
    } catch {}
    setIsSaving(false);
  };

  return (
    <div className="flex h-full w-full bg-[#06090f] overflow-hidden text-slate-200">
      {/* ── SIDEBAR ────────────────────────────────────── */}
      <div className="w-56 border-r border-white/[0.06] bg-[#080c14] flex flex-col shrink-0">
        <div className="p-3 text-xs tracking-widest text-slate-500 font-mono uppercase bg-black/20 border-b border-white/[0.05]">
          Active Agents
        </div>
        <div className="flex-1 overflow-y-auto py-2">
          {agentStatuses.map((agent: AgentStatus) => (
            <button
              key={agent.name}
              onClick={() => setSelectedAgent(agent.name)}
              className={`w-full flex items-center justify-between px-4 py-2 text-left group transition-all duration-200
                ${selectedAgent === agent.name ? "bg-blue-500/10 text-blue-400" : "text-slate-400 hover:bg-white/[0.03] hover:text-slate-200"}`}
            >
              <div className="flex items-center gap-2">
                <Terminal className="w-3.5 h-3.5" />
                <span className="font-mono text-[12px]">{agent.name}.md</span>
              </div>
              
              {/* CHANGED: Item 11 - Graphical visual Badges reflecting CSP and Parse status */}
              <div className="relative flex items-center group/tooltip">
                {agent.is_valid ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)] rounded-full" />
                ) : (
                  <AlertTriangle className="w-3.5 h-3.5 text-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.5)] rounded-full" />
                )}
                
                {/* Error Tooltip rendering */}
                {!agent.is_valid && (
                  <div className="absolute right-0 top-full mt-1 hidden group-hover/tooltip:block z-50 w-64 p-2 bg-rose-950/90 border border-rose-500/30 rounded text-[10px] shadow-xl text-rose-200">
                    <div className="flex gap-1.5 font-bold mb-1 items-center">
                      <XCircle className="w-3 h-3" /> Parse Error
                    </div>
                    {agent.error}
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* ── EDITOR AREA ────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 bg-[#06090f]">
        {selectedAgent ? (
          <>
            <div className="flex items-center justify-between px-4 py-2 border-b border-white/[0.05] bg-[#0a0f18]">
              <div className="flex items-center gap-2 text-[12px] font-mono text-slate-400">
                <Sparkles className="w-3.5 h-3.5 text-blue-400" />
                Editing <span className="text-slate-200">agents/{selectedAgent}.md</span>
                {isDirty && <span className="text-amber-500 ml-2 animate-pulse">• Unsaved</span>}
              </div>
              <button
                onClick={handleSave}
                disabled={isSaving || !isDirty}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-mono tracking-widest transition-all
                  ${!isDirty ? 'opacity-50 cursor-not-allowed bg-white/5 text-slate-500' : 
                   isSaving ? 'bg-blue-600/50 text-white animate-pulse' : 
                   'bg-blue-600 hover:bg-blue-500 text-white shadow-[0_0_15px_rgba(37,99,235,0.4)]'}`}
              >
                <Save className="w-3.5 h-3.5" />
                {isSaving ? "SAVING..." : "COMMIT"}
              </button>
            </div>
            
            <div className="flex-1 relative">
              <MonacoEditor
                height="100%"
                language="markdown"
                theme="vs-dark"
                value={content}
                onChange={(val) => {
                  setContent(val || "");
                  setIsDirty(true);
                }}
                options={{
                  minimap: { enabled: false },
                  fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                  fontSize: 13,
                  lineHeight: 22,
                  padding: { top: 16 },
                  scrollBeyondLastLine: false,
                  wordWrap: "on",
                  matchBrackets: "never",
                  renderLineHighlight: "all"
                }}
              />
            </div>
          </>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-slate-600">
            <Search className="w-10 h-10 mb-4 opacity-50" />
            <p className="font-mono text-[12px] uppercase tracking-widest">Select an Agent to edit</p>
          </div>
        )}
      </div>
    </div>
  );
}
