"use client";

import type { OnMount } from "@monaco-editor/react";
import dynamic from "next/dynamic";
import { useEffect, useId, useState } from "react";

import { cn } from "@/lib/utils";

const MonacoEditor = dynamic(
  () => import("@monaco-editor/react").then((module) => module.default),
  { ssr: false },
);

type PythonCodeEditorProps = {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  /** Alias for readOnly — ready for edit-lock wiring. */
  options?: { readOnly?: boolean };
  id?: string;
  className?: string;
  placeholder?: string;
  height?: number | string;
};

const ATLAS_LIGHT = "atlas-light";
const ATLAS_DARK = "atlas-dark";

function isAdminDark(): boolean {
  if (typeof document === "undefined") return false;
  return Boolean(document.querySelector('.app-canvas[data-theme="dark"]'));
}

function defineAtlasThemes(monaco: Parameters<OnMount>[1]) {
  monaco.editor.defineTheme(ATLAS_LIGHT, {
    base: "vs",
    inherit: true,
    rules: [],
    colors: {
      "editor.background": "#ffffff",
      "editor.foreground": "#071018",
      "editor.lineHighlightBackground": "#eef3f7",
      "editorLineNumber.foreground": "#5b6b78",
      "editorLineNumber.activeForeground": "#071018",
      "editorCursor.foreground": "#0f8f7b",
      "editor.selectionBackground": "#0f8f7b33",
      "editor.inactiveSelectionBackground": "#0f8f7b1a",
      "editorIndentGuide.background": "#c5d0da",
      "editorIndentGuide.activeBackground": "#9db0bf",
      "editorWidget.background": "#ffffff",
      "editorWidget.border": "#c5d0da",
      "scrollbarSlider.background": "#9db0bf66",
      "scrollbarSlider.hoverBackground": "#9db0bf99",
    },
  });

  monaco.editor.defineTheme(ATLAS_DARK, {
    base: "vs-dark",
    inherit: true,
    rules: [],
    colors: {
      "editor.background": "#10222d",
      "editor.foreground": "#e4f0f2",
      "editor.lineHighlightBackground": "#0a1a23",
      "editorLineNumber.foreground": "#7f98a2",
      "editorLineNumber.activeForeground": "#e4f0f2",
      "editorCursor.foreground": "#22c8a8",
      "editor.selectionBackground": "#22c8a844",
      "editor.inactiveSelectionBackground": "#22c8a822",
      "editorIndentGuide.background": "#1c3440",
      "editorIndentGuide.activeBackground": "#2d4b58",
      "editorWidget.background": "#10222d",
      "editorWidget.border": "#1c3440",
      "scrollbarSlider.background": "#2d4b5866",
      "scrollbarSlider.hoverBackground": "#2d4b5899",
    },
  });
}

function disableSpellcheck(domNode: HTMLElement | null) {
  if (!domNode) return;
  domNode.setAttribute("spellcheck", "false");
  for (const el of domNode.querySelectorAll("textarea, [contenteditable]")) {
    el.setAttribute("spellcheck", "false");
  }
}

/**
 * Monaco-based Python source editor for tenant_python tools.
 * Client-only; follows AdminShell light/dark via data-theme on .app-canvas.
 */
export function PythonCodeEditor({
  value,
  onChange,
  readOnly = false,
  options,
  id,
  className,
  height = 360,
}: PythonCodeEditorProps) {
  const autoId = useId();
  const editorId = id ?? autoId;
  const locked = Boolean(readOnly || options?.readOnly);
  const [theme, setTheme] = useState<typeof ATLAS_LIGHT | typeof ATLAS_DARK>(
    ATLAS_LIGHT,
  );

  useEffect(() => {
    const sync = () =>
      setTheme(isAdminDark() ? ATLAS_DARK : ATLAS_LIGHT);
    sync();
    // Theme lives on `.app-canvas` only — do not observe body with subtree
    // (Monaco/DOM churn would re-fire on every keystroke).
    const root = document.querySelector(".app-canvas");
    if (!root) return;
    const observer = new MutationObserver(sync);
    observer.observe(root, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => observer.disconnect();
  }, []);

  const handleMount: OnMount = (editor, monaco) => {
    defineAtlasThemes(monaco);
    monaco.editor.setTheme(theme);
    disableSpellcheck(editor.getDomNode());
    // Re-apply if Monaco recreates the input textarea.
    const dom = editor.getDomNode();
    if (dom) {
      const mo = new MutationObserver(() => disableSpellcheck(dom));
      mo.observe(dom, { childList: true, subtree: true });
      editor.onDidDispose(() => mo.disconnect());
    }
  };

  return (
    <div
      id={editorId}
      spellCheck={false}
      className={cn(
        "overflow-hidden rounded-md border border-line bg-raised",
        locked && "opacity-90",
        className,
      )}
      style={{ minHeight: typeof height === "number" ? height : undefined }}
    >
      <MonacoEditor
        height={height}
        language="python"
        theme={theme}
        value={value}
        onChange={(next) => {
          if (locked) return;
          onChange?.(next ?? "");
        }}
        onMount={handleMount}
        loading={
          <div className="flex h-full min-h-[360px] items-center justify-center bg-raised text-sm text-slate-muted">
            Loading editor…
          </div>
        }
        options={{
          readOnly: locked,
          minimap: { enabled: false },
          fontSize: 13,
          fontFamily: "var(--font-mono), IBM Plex Mono, ui-monospace, monospace",
          lineNumbers: "on",
          wordWrap: "on",
          scrollBeyondLastLine: false,
          automaticLayout: true,
          tabSize: 4,
          insertSpaces: true,
          renderLineHighlight: locked ? "none" : "line",
          padding: { top: 12, bottom: 12 },
          scrollbar: {
            verticalScrollbarSize: 10,
            horizontalScrollbarSize: 10,
          },
          overviewRulerLanes: 0,
          hideCursorInOverviewRuler: true,
          overviewRulerBorder: false,
          folding: true,
          glyphMargin: false,
          quickSuggestions: false,
          suggestOnTriggerCharacters: false,
          parameterHints: { enabled: false },
          wordBasedSuggestions: "off",
          contextmenu: !locked,
          domReadOnly: locked,
        }}
      />
    </div>
  );
}
