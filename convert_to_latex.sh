#!/usr/bin/env bash
# convert_to_latex.sh — Convert a pipeline .md report to PDF or .tex via pandoc
#
# Requirements:
#   brew install pandoc
#   brew install --cask mactex      # or texlive on Linux
#
# Usage:
#   ./convert_to_latex.sh report.md             # → report.pdf  (default)
#   ./convert_to_latex.sh report.md pdf         # → report.pdf
#   ./convert_to_latex.sh report.md tex         # → report.tex  (editable LaTeX source)
#   ./convert_to_latex.sh report.md both        # → report.pdf + report.tex
#
# The .md files already contain a YAML front matter block (prepended by the
# pipeline) that sets: title, date, margins, font size, TOC, section numbering,
# booktabs, longtable, float, fancyhdr.  No extra flags are needed.

set -euo pipefail

INPUT="${1:-}"
MODE="${2:-pdf}"

if [[ -z "$INPUT" ]]; then
    echo "Usage: $0 <report.md> [pdf|tex|both]"
    exit 1
fi

if [[ ! -f "$INPUT" ]]; then
    echo "Error: file not found: $INPUT"
    exit 1
fi

BASENAME="${INPUT%.md}"
BASENAME="${BASENAME%.MD}"

# ── Shared pandoc flags ────────────────────────────────────────────────────────
#
# --from markdown+tex_math_dollars
#   Enables $...$ and $$...$$ math parsing (already the default for XeLaTeX
#   output but stated explicitly for clarity).
#
# --pdf-engine=xelatex
#   XeLaTeX handles Unicode (×, ≥, →, etc.) natively. Preferred over pdflatex.
#
# --highlight-style=kate
#   Syntax highlighting for any code blocks in the report.
#
PANDOC_FLAGS=(
    --from "markdown+tex_math_dollars"
    --highlight-style kate
    --pdf-engine xelatex
)

make_pdf() {
    local out="${BASENAME}.pdf"
    echo "→ Converting to PDF: $out"
    pandoc "$INPUT" "${PANDOC_FLAGS[@]}" -o "$out"
    echo "  Done: $out"
}

make_tex() {
    local out="${BASENAME}.tex"
    echo "→ Converting to .tex: $out"
    pandoc "$INPUT" \
        --from "markdown+tex_math_dollars" \
        --highlight-style kate \
        --standalone \
        -o "$out"
    echo "  Done: $out"
    echo "  Compile with: xelatex $out"
}

case "$MODE" in
    pdf)  make_pdf ;;
    tex)  make_tex ;;
    both) make_pdf; make_tex ;;
    *)
        echo "Unknown mode '$MODE'. Use: pdf | tex | both"
        exit 1
        ;;
esac
