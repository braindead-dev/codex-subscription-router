// Presentation only: retain the original envelope in the model's input/history.
// Only a complete, standalone envelope gets a session card; quoted examples and
// messages with attachments keep the ordinary renderer.
function codexMuxParseSessionMessage(text) {
  if (typeof text !== "string") return null;
  const envelope = /^\s*<cross-session-message((?:\s+[\w-]+=(?:"[^"<>]*"|'[^'<>]*'))+)\s*>([\s\S]*)<\/cross-session-message>\s*$/.exec(text);
  if (!envelope || /<\/?cross-session-message(?:\s|>)/.test(envelope[2])) return null;
  const attributes = new Map();
  for (const match of envelope[1].matchAll(/([\w-]+)=(?:"([^"]*)"|'([^']*)')/g)) {
    if (attributes.has(match[1])) return null;
    attributes.set(match[1], codexMuxSessionAttribute(match[2] ?? match[3]));
  }
  const address = attributes.get("from")?.trim();
  if (!address) return null;
  return {
    address,
    name: attributes.get("from-name")?.trim() || "Another session",
    body: envelope[2].trim(),
  };
}

function codexMuxSessionAttribute(text) {
  const entities = { amp: "&", quot: '"', apos: "'", lt: "<", gt: ">" };
  return text.replace(/&(#x[\da-f]+|#\d+|amp|quot|apos|lt|gt);/gi, (entity, code) => {
    if (code[0] !== "#") return entities[code.toLowerCase()];
    const value = code[1].toLowerCase() === "x"
      ? Number.parseInt(code.slice(2), 16)
      : Number.parseInt(code.slice(1), 10);
    return value > 0 && value <= 0x10ffff && !(value >= 0xd800 && value <= 0xdfff)
      ? String.fromCodePoint(value)
      : entity;
  });
}

function codexMuxSessionMessage(item, context, NativeMessage) {
  if (["images", "attachments", "uploadedFileAttachments", "unavailableFileAttachments",
    "comments", "selectedTextAttachments", "responseAnnotations", "appshotContexts",
    "mcpAppModelContextAttachments"].some((key) => item[key]?.length)) return null;
  const message = codexMuxParseSessionMessage(item.message);
  if (!message) return null;
  const { jsx, jsxs } = globalThis.codexMuxJsx();
  return jsx("div", {
    "data-codex-mux-session-message": "",
    className: "w-full min-w-0",
    children: jsx(NativeMessage, {
      ...context,
      alignment: "start",
      message: message.body,
      sentAtMs: item.sentAtMs,
      label: jsxs("span", {
        className: "flex min-w-0 items-center gap-1.5",
        title: message.address,
        children: [
          jsx("svg", {
            width: 16, height: 16, viewBox: "0 0 24 24", fill: "none",
            stroke: "currentColor", strokeWidth: 1.5, "aria-hidden": true,
            className: "shrink-0",
            children: jsx("path", { d: "M4 5h16v11H9l-5 4V5Zm4 5h8m-3-3 3 3-3 3", strokeLinecap: "round", strokeLinejoin: "round" }),
          }),
          jsx("span", { className: "truncate", children: message.name }),
          jsx("span", { className: "shrink-0 opacity-60", children: "· Session message" }),
        ],
      }),
    }),
  });
}

globalThis.codexMuxSessionMessage = codexMuxSessionMessage;
