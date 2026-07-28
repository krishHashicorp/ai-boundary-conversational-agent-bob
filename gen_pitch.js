const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, PageBreak, LevelFormat
} = require('docx');
const fs = require('fs');

// ── Colours ──────────────────────────────────────────────────────────────────
const IBM_BLUE  = "0F62FE";
const MID_BLUE  = "1B4F9A";
const LIGHT_BG  = "EBF1FB";
const HEADER_BG = "D5E4F7";
const GRAY_BG   = "F4F4F4";
const BORDER_C  = "C6D3EC";
const WHITE     = "FFFFFF";
const BLACK     = "161616";

// ── Shared border definition ─────────────────────────────────────────────────
const b = { style: BorderStyle.SINGLE, size: 1, color: BORDER_C };
const cellBorder = { top: b, bottom: b, left: b, right: b };

// ── Helpers ───────────────────────────────────────────────────────────────────
function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 280, after: 120 },
    children: [new TextRun({ text, bold: true, size: 30, color: MID_BLUE, font: "Arial" })]
  });
}
function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 220, after: 80 },
    children: [new TextRun({ text, bold: true, size: 26, color: IBM_BLUE, font: "Arial" })]
  });
}
function body(text, opts = {}) {
  return new Paragraph({
    spacing: { before: 60, after: 80 },
    children: [new TextRun({ text, size: 22, color: BLACK, font: "Arial", ...opts })]
  });
}
function gap(pt = 60) {
  return new Paragraph({ spacing: { before: pt, after: 0 }, children: [new TextRun("")] });
}
function bullet(text, ref = "bullets") {
  return new Paragraph({
    numbering: { reference: ref, level: 0 },
    spacing: { before: 40, after: 40 },
    children: [new TextRun({ text, size: 22, color: BLACK, font: "Arial" })]
  });
}
function boldBullet(label, rest, ref = "bullets") {
  return new Paragraph({
    numbering: { reference: ref, level: 0 },
    spacing: { before: 40, after: 40 },
    children: [
      new TextRun({ text: label, bold: true, size: 22, color: BLACK, font: "Arial" }),
      new TextRun({ text: rest,  size: 22, color: BLACK, font: "Arial" })
    ]
  });
}
function hCell(text, fill = HEADER_BG, colSpan) {
  return new TableCell({
    borders: cellBorder,
    shading: { fill, type: ShadingType.CLEAR },
    verticalAlign: VerticalAlign.CENTER,
    columnSpan: colSpan,
    children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({ text, bold: true, size: 20, color: BLACK, font: "Arial" })]
    })]
  });
}
function dCell(text, fill = WHITE, align = AlignmentType.LEFT) {
  return new TableCell({
    borders: cellBorder,
    shading: { fill, type: ShadingType.CLEAR },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({
      alignment: align,
      spacing: { before: 60, after: 60 },
      children: [new TextRun({ text, size: 20, color: BLACK, font: "Arial" })]
    })]
  });
}
function bCell(text, fill = WHITE) {
  return new TableCell({
    borders: cellBorder,
    shading: { fill, type: ShadingType.CLEAR },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({
      spacing: { before: 60, after: 60 },
      children: [new TextRun({ text, bold: true, size: 20, color: IBM_BLUE, font: "Arial" })]
    })]
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// PAGE 1 — Cover & Problem + Solution
// ═══════════════════════════════════════════════════════════════════════════════
const page1 = [
  // Title block
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 400, after: 60 },
    children: [new TextRun({ text: "AI Boundary Agent", bold: true, size: 64, color: IBM_BLUE, font: "Arial" })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 60 },
    children: [new TextRun({ text: "Secure, Conversational Infrastructure Access Powered by IBM WatsonX", size: 28, color: MID_BLUE, italics: true, font: "Arial" })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 320 },
    children: [new TextRun({ text: "Krishnan Ramachandran  ·  IBM Software  ·  June 2025", size: 22, color: "6F6F6F", font: "Arial" })]
  }),

  // ── Section 1: The Problem ──
  h1("1.  The Problem"),
  body("Every day, platform engineers and SREs perform the same slow ritual: open a terminal, look up a hostname, find credentials, verify permissions, and only then run the handful of commands they actually needed. Multiply this by dozens of servers, a rotating on-call schedule, and audit-compliance requirements, and the hidden cost is enormous."),
  gap(),
  body("Three specific pain points drive this project:", { bold: true }),
  gap(40),
  boldBullet("Credential sprawl — ", "SSH keys and passwords are scattered across team members' machines, raising the blast radius of any single compromise.", "bullets1"),
  boldBullet("No audit trail — ", "Ad-hoc terminal sessions leave no record of who ran what command, on which server, at what time.", "bullets1"),
  boldBullet("High cognitive overhead — ", "Operators must translate a business question ('is this host healthy?') into a sequence of shell commands before they can even begin.", "bullets1"),

  gap(120),

  // ── Section 2: The Solution ──
  h1("2.  The Solution — AI Boundary Agent"),
  body("A conversational AI agent that connects to any remote Linux host through HCP Boundary and executes shell commands on the operator's behalf — powered by IBM Granite via WatsonX.ai. The operator asks a question in plain English; the agent handles everything else."),
  gap(),

  // Solution highlights table
  new Table({
    columnWidths: [2200, 6800],
    margins: { top: 80, bottom: 80, left: 160, right: 160 },
    rows: [
      new TableRow({ tableHeader: true, children: [hCell("Pillar", HEADER_BG), hCell("What It Delivers", HEADER_BG)] }),
      new TableRow({ children: [bCell("Natural Language"), dCell("Ask 'What are the top CPU processes?' — no shell expertise needed.")] }),
      new TableRow({ children: [bCell("Zero-Trust Access"), dCell("HCP Boundary brokers every connection; no VPN, no standing SSH access.")] }),
      new TableRow({ children: [bCell("Credential Injection"), dCell("Boundary injects the SSH password at the proxy layer — the agent never holds credentials.")] }),
      new TableRow({ children: [bCell("Continuous Audit"), dCell("Every session is recorded in the Boundary admin console as a single interactive session.")] }),
      new TableRow({ children: [bCell("Prompt Safety"), dCell("Embedded safety rules block destructive commands (rm -rf, shutdown, iptables changes, etc.).")] }),
    ]
  }),

  // Page break before page 2
  new Paragraph({ children: [new PageBreak()] })
];

// ═══════════════════════════════════════════════════════════════════════════════
// PAGE 2 — How Bob & ICA Were Used + Flow Diagram
// ═══════════════════════════════════════════════════════════════════════════════
const page2 = [
  h1("3.  How Bob and ICA Were Used"),
  body("The entire project was built inside a Bob (ICA) playground session. Bob was the co-author at every stage, not just a code assistant:"),
  gap(40),
  boldBullet("Architecture design — ", "Bob proposed the four-module structure (main.py / agent.py / boundary_session.py / ssh_exec.py) and the ReAct (Reason + Act) agent loop pattern.", "bullets2"),
  boldBullet("Code generation — ", "Bob scaffolded all four source modules, the 24-test offline test suite, and the .env.example template from a single architectural description.", "bullets2"),
  boldBullet("Debugging — ", "When the Boundary CLI rejected plain-string passwords (v0.21 security change) and when boundary connect ssh -style=none was found to be invalid, Bob diagnosed root causes and produced the corrected code in the same turn.", "bullets2"),
  boldBullet("Documentation — ", "Bob authored README.md, ARCHITECTURE.md, and BLOG.md — the latter as a publication-ready technical post covering every design decision.", "bullets2"),
  boldBullet("Pitch creation — ", "This document was produced by Bob from project artefacts in a single prompt, formatted as a professional 3-page Word pitch.", "bullets2"),
  gap(80),
  body("Estimated time saved: a project that would typically take 3–5 days of focused engineering was completed in approximately 6 hours — roughly a 5–8× acceleration.", { bold: true }),
  gap(120),

  // ── Section 4: Flow Diagram ──
  h1("4.  How It Works — Conversation Flow"),
  body("The diagram below traces a single operator turn from question to answer:"),
  gap(60),

  // ASCII-style flow table
  new Table({
    columnWidths: [1400, 440, 1400, 440, 1400, 440, 1400, 440, 1400],
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    rows: [
      new TableRow({
        children: [
          new TableCell({ borders: cellBorder, shading: { fill: LIGHT_BG, type: ShadingType.CLEAR }, children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Operator", bold: true, size: 18, font: "Arial", color: MID_BLUE })] }), new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Plain-English question", size: 16, font: "Arial" })] })] }),
          new TableCell({ borders: { top: { style: BorderStyle.NONE }, bottom: { style: BorderStyle.NONE }, left: { style: BorderStyle.NONE }, right: { style: BorderStyle.NONE } }, shading: { fill: WHITE, type: ShadingType.CLEAR }, children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "->", bold: true, size: 22, color: IBM_BLUE, font: "Arial" })] })] }),
          new TableCell({ borders: cellBorder, shading: { fill: LIGHT_BG, type: ShadingType.CLEAR }, children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "WatsonX LLM", bold: true, size: 18, font: "Arial", color: MID_BLUE })] }), new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Reasons in JSON", size: 16, font: "Arial" })] })] }),
          new TableCell({ borders: { top: { style: BorderStyle.NONE }, bottom: { style: BorderStyle.NONE }, left: { style: BorderStyle.NONE }, right: { style: BorderStyle.NONE } }, shading: { fill: WHITE, type: ShadingType.CLEAR }, children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "->", bold: true, size: 22, color: IBM_BLUE, font: "Arial" })] })] }),
          new TableCell({ borders: cellBorder, shading: { fill: LIGHT_BG, type: ShadingType.CLEAR }, children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "HCP Boundary", bold: true, size: 18, font: "Arial", color: MID_BLUE })] }), new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Auth + proxy tunnel", size: 16, font: "Arial" })] })] }),
          new TableCell({ borders: { top: { style: BorderStyle.NONE }, bottom: { style: BorderStyle.NONE }, left: { style: BorderStyle.NONE }, right: { style: BorderStyle.NONE } }, shading: { fill: WHITE, type: ShadingType.CLEAR }, children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "->", bold: true, size: 22, color: IBM_BLUE, font: "Arial" })] })] }),
          new TableCell({ borders: cellBorder, shading: { fill: LIGHT_BG, type: ShadingType.CLEAR }, children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Ubuntu Host", bold: true, size: 18, font: "Arial", color: MID_BLUE })] }), new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Shell command runs", size: 16, font: "Arial" })] })] }),
          new TableCell({ borders: { top: { style: BorderStyle.NONE }, bottom: { style: BorderStyle.NONE }, left: { style: BorderStyle.NONE }, right: { style: BorderStyle.NONE } }, shading: { fill: WHITE, type: ShadingType.CLEAR }, children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "<-", bold: true, size: 22, color: IBM_BLUE, font: "Arial" })] })] }),
          new TableCell({ borders: cellBorder, shading: { fill: HEADER_BG, type: ShadingType.CLEAR }, children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Plain-English Answer", bold: true, size: 18, font: "Arial", color: MID_BLUE })] }), new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Returned to operator", size: 16, font: "Arial" })] })] }),
        ]
      })
    ]
  }),

  gap(80),
  body("Session model: the agent connects once per conversation. All commands reuse the same persistent SSH shell through the Boundary proxy tunnel — preserving working directory, environment variables, and shell state. Boundary records the entire conversation as a single interactive session in its audit console."),

  gap(80),

  // Live demo snippet
  h2("Live Demo Transcript"),
  new Table({
    columnWidths: [9000],
    margins: { top: 80, bottom: 80, left: 200, right: 200 },
    rows: [
      new TableRow({ children: [new TableCell({ borders: cellBorder, shading: { fill: "1C1C1C", type: ShadingType.CLEAR }, children: [
        new Paragraph({ children: [new TextRun({ text: "[disconnected]  You: Connect to the host and show me disk usage", size: 18, color: "98C379", font: "Courier New" })] }),
        new Paragraph({ children: [new TextRun({ text: "Agent: Connected. Disk usage:", size: 18, color: "ABB2BF", font: "Courier New" })] }),
        new Paragraph({ children: [new TextRun({ text: "  /dev/root  7.6G  3.2G  4.5G  42%  /", size: 18, color: "ABB2BF", font: "Courier New" })] }),
        new Paragraph({ children: [new TextRun({ text: "", size: 18, font: "Courier New" })] }),
        new Paragraph({ children: [new TextRun({ text: "[connected ✓]  You: Check available memory", size: 18, color: "98C379", font: "Courier New" })] }),
        new Paragraph({ children: [new TextRun({ text: "Agent: 3.5 GB used of 8 GB total, 4.5 GB free.", size: 18, color: "ABB2BF", font: "Courier New" })] }),
        new Paragraph({ children: [new TextRun({ text: "", size: 18, font: "Courier New" })] }),
        new Paragraph({ children: [new TextRun({ text: "[connected ✓]  You: What are the top CPU processes?", size: 18, color: "98C379", font: "Courier New" })] }),
        new Paragraph({ children: [new TextRun({ text: "Agent: 1. python3 (12.4%)  2. nginx (3.1%)  3. sshd (0.4%)", size: 18, color: "ABB2BF", font: "Courier New" })] }),
        new Paragraph({ children: [new TextRun({ text: "", size: 18, font: "Courier New" })] }),
        new Paragraph({ children: [new TextRun({ text: "[connected ✓]  You: Disconnect", size: 18, color: "98C379", font: "Courier New" })] }),
        new Paragraph({ children: [new TextRun({ text: "Agent: Host is healthy. SSH closed. Boundary session terminated.", size: 18, color: "ABB2BF", font: "Courier New" })] }),
      ] })] }),
    ]
  }),

  new Paragraph({ children: [new PageBreak()] })
];

// ═══════════════════════════════════════════════════════════════════════════════
// PAGE 3 — Productivity Impact + Roadmap
// ═══════════════════════════════════════════════════════════════════════════════
const page3 = [
  h1("5.  Productivity Impact"),
  body("The agent eliminates the three-step overhead of every infrastructure query: credential lookup, SSH negotiation, and command formulation. Below is a before/after comparison for a typical triage scenario:"),
  gap(60),

  // Before/After table
  new Table({
    columnWidths: [2500, 3200, 3200],
    margins: { top: 80, bottom: 80, left: 160, right: 160 },
    rows: [
      new TableRow({ tableHeader: true, children: [hCell("Task", HEADER_BG), hCell("Before (manual)", HEADER_BG), hCell("After (AI Boundary Agent)", HEADER_BG)] }),
      new TableRow({ children: [dCell("Connect to host"), dCell("~3 min: find creds, open terminal, SSH"), dCell("~5 sec: 'Connect to the host'")] }),
      new TableRow({ children: [dCell("Triage 5 metrics"), dCell("~15 min: 5 separate commands, interpret each"), dCell("~45 sec: 1 conversational question")] }),
      new TableRow({ children: [dCell("Leave audit record"), dCell("~10 min: manual runbook entry or none"), dCell("0 min: Boundary records automatically")] }),
      new TableRow({ children: [dCell("Rotate credentials"), dCell("~30 min: update team vault, notify members"), dCell("0 min: Boundary injects ephemerally")] }),
      new TableRow({ children: [dCell("On-board new SRE"), dCell("~1 day: SSH setup, credentials, runbooks"), dCell("~10 min: chat interface needs no shell expertise")] }),
    ]
  }),

  gap(80),
  body("Estimated engineering time saved per triage incident: ~25 minutes. Across a 10-person platform team averaging 3 incidents per week, this compounds to roughly 65 hours/month — equivalent to approximately 1.5 FTE weeks of recovered capacity per month."),
  gap(60),
  body("Build time: the entire working prototype — 4 source modules, 24 tests, 3 documentation files — was completed in approximately 6 hours with Bob as co-author, versus an estimated 3–5 engineering days without AI assistance. That is a 5–8× development acceleration."),

  gap(120),

  // ── Section 6: Roadmap ──
  h1("6.  Roadmap for Future Iterations"),
  gap(40),

  // Roadmap table
  new Table({
    columnWidths: [1600, 1200, 5600],
    margins: { top: 80, bottom: 80, left: 160, right: 160 },
    rows: [
      new TableRow({ tableHeader: true, children: [hCell("Phase", HEADER_BG), hCell("Timeline", HEADER_BG), hCell("Capability", HEADER_BG)] }),
      new TableRow({ children: [bCell("Phase 1", GRAY_BG), dCell("Now", GRAY_BG), dCell("Working prototype: WatsonX + Boundary + SSH. Password auth. Safety rules in system prompt. 24 offline tests.", GRAY_BG)] }),
      new TableRow({ children: [bCell("Phase 2"), dCell("4–6 wks"), dCell("OIDC authentication via IBM Verify — replace service-account password with MFA-gated SSO for operators.")] }),
      new TableRow({ children: [bCell("Phase 3"), dCell("6–10 wks"), dCell("HashiCorp Vault integration — dynamically fetch WatsonX API key and Boundary credentials at runtime; eliminate all static secrets from .env.")] }),
      new TableRow({ children: [bCell("Phase 4"), dCell("10–14 wks"), dCell("Multi-target fleet support — manage groups of hosts, run comparative queries, auto-detect anomalies across a fleet.")] }),
      new TableRow({ children: [bCell("Phase 5"), dCell("14–20 wks"), dCell("Web UI — React front-end with session history, replay of Boundary recordings, and role-based access for non-CLI users.")] }),
    ]
  }),

  gap(120),

  // ── Section 7: Call to Action ──
  h1("7.  Why This Matters for IBM"),
  body("The AI Boundary Agent is a concrete demonstration that IBM's own stack — WatsonX.ai, IBM Granite, IBM Verify, and HCP Boundary — can be composed into an agentic, zero-trust infrastructure access tool that is both safer and dramatically faster than today's manual workflows."),
  gap(40),
  body("It also validates a working pattern for how Bob (ICA) can accelerate complex technical projects: not just as a code assistant but as a pair-programmer that contributes architecture, debugging, documentation, and communication artefacts — all within a single, auditable workspace."),
  gap(80),
  body("The full source code, tests, and documentation live in the ai-boundary-agent playground repository.", { italics: true }),
];

// ═══════════════════════════════════════════════════════════════════════════════
// Assemble Document
// ═══════════════════════════════════════════════════════════════════════════════
const doc = new Document({
  numbering: {
    config: [
      { reference: "bullets",  levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 260 } } } }] },
      { reference: "bullets1", levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 260 } } } }] },
      { reference: "bullets2", levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 260 } } } }] },
    ]
  },
  styles: {
    default: { document: { run: { font: "Arial", size: 22, color: BLACK } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run:  { size: 30, bold: true, color: MID_BLUE, font: "Arial" },
        paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run:  { size: 26, bold: true, color: IBM_BLUE, font: "Arial" },
        paragraph: { spacing: { before: 220, after: 80 }, outlineLevel: 1 } },
    ]
  },
  sections: [{
    properties: {
      page: { margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 } }
    },
    headers: {
      default: new Header({ children: [
        new Paragraph({
          alignment: AlignmentType.RIGHT,
          border: { bottom: { style: BorderStyle.SINGLE, size: 1, color: BORDER_C } },
          spacing: { after: 80 },
          children: [new TextRun({ text: "AI Boundary Agent  |  Pitch Deck  |  June 2025", size: 18, color: "6F6F6F", font: "Arial" })]
        })
      ] })
    },
    footers: {
      default: new Footer({ children: [
        new Paragraph({
          alignment: AlignmentType.CENTER,
          border: { top: { style: BorderStyle.SINGLE, size: 1, color: BORDER_C } },
          spacing: { before: 80 },
          children: [
            new TextRun({ text: "Page ", size: 18, color: "6F6F6F", font: "Arial" }),
            new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "6F6F6F", font: "Arial" }),
            new TextRun({ text: " of ", size: 18, color: "6F6F6F", font: "Arial" }),
            new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 18, color: "6F6F6F", font: "Arial" }),
            new TextRun({ text: "  ·  Confidential — IBM Internal", size: 18, color: "6F6F6F", font: "Arial" }),
          ]
        })
      ] })
    },
    children: [...page1, ...page2, ...page3]
  }]
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync("pitch-ai-boundary-agent.docx", buf);
  console.log("Done: pitch-ai-boundary-agent.docx");
});
