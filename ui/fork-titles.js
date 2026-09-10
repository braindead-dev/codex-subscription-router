// A fork inherits its parent's title with a counter and is never titled
// again, because the first-message title generator only runs for untitled
// threads. Once the fork's first turn completes, this asks the same
// generator for a title of its own, telling it what the original chat was
// about and what the fork now asks.
function codexMuxForkTitles(store, conversations) {
  const inFlight = new Set();
  conversations.addTurnCompletedListener((event) => {
    if (
      event.status !== "completed" ||
      event.turnId == null ||
      event.hasPendingContinuation ||
      inFlight.has(event.conversationId) ||
      conversations.getStreamRole(event.conversationId)?.role !== "owner"
    ) {
      return;
    }
    const conversation = conversations.getConversation(event.conversationId);
    if (
      conversation == null ||
      conversation.forkedFromId == null ||
      conversation.ephemeral === true ||
      conversation.sideConversation === true
    ) {
      return;
    }
    const title = conversation.title?.trim() ?? "";
    const parent = conversations.getConversation(conversation.forkedFromId);
    const base = codexMuxForkBaseTitle(title, parent?.title);
    if (base == null) return;
    const request = codexMuxForkFirstMessage(
      codexMuxTurnWithId(conversation, event.turnId),
    );
    if (request.length === 0) return;
    inFlight.add(event.conversationId);
    codexMuxTitleFork(store, conversations, {
      conversationId: event.conversationId,
      title,
      base,
      context: codexMuxForkContext(conversation, event.turnId),
      request,
    }).finally(() => inFlight.delete(event.conversationId));
  });
}

async function codexMuxTitleFork(
  store,
  conversations,
  { conversationId, title, base, context, request },
) {
  try {
    const conversation = conversations.getConversation(conversationId);
    const result = await CODEX_MUX_SERVICES.threadMetadataGeneration?.generateTitle({
      hostId: conversations.getHostId(),
      prompt: codexMuxForkTitlePrompt(base, context, request),
      cwd: conversations.getConversationCwd(conversationId),
      readOnlyAppToolAllowlist: [],
      ...(conversation?.serviceName === undefined
        ? {}
        : { serviceName: conversation.serviceName }),
    });
    const next = result?.title?.trim() ?? "";
    if (
      next.length === 0 ||
      next === title ||
      conversations.getConversation(conversationId)?.title?.trim() !== title
    ) {
      return;
    }
    const renamed = await conversations.setThreadTitle(conversationId, next, {
      expectedTitle: title,
      source: "generated",
    });
    const description = result?.description?.trim() ?? "";
    if (renamed && description.length > 0) {
      codexMuxRememberDescription(store, conversationId, description);
    }
  } catch {}
}

// codexMuxForkBaseTitle returns the parent's title when the fork still
// carries the counter the desktop appended at fork time, or null once the
// user renamed it.
function codexMuxForkBaseTitle(title, parentTitle) {
  const match = /^(.+) \((\d+)\)$/.exec(title);
  if (match == null) return null;
  const base = match[1];
  const parent = parentTitle?.trim() ?? "";
  if (parent.length === 0 || parent === base) return base;
  const parentMatch = /^(.+) \((\d+)\)$/.exec(parent);
  return parentMatch != null && parentMatch[1] === base ? base : null;
}

function codexMuxForkFirstMessage(turn) {
  return codexMuxTurnText(turn, "userMessage").slice(0, 2_000);
}

// codexMuxForkContext gathers the last few exchanges before the fork's
// first turn, which is the history the fork continues from.
function codexMuxForkContext(conversation, turnId) {
  const turns = codexMuxConversationTurns(conversation) ?? [];
  const index = turns.findIndex((turn) => turn.turnId === turnId);
  const before = (index < 0 ? turns : turns.slice(0, index)).slice(-4);
  return before
    .flatMap((turn) => [
      codexMuxLabelled("User", codexMuxTurnText(turn, "userMessage")),
      codexMuxLabelled("Assistant", codexMuxTurnText(turn, "agentMessage")),
    ])
    .filter(Boolean)
    .join("\n");
}

function codexMuxLabelled(label, text) {
  const trimmed = text.trim();
  return trimmed.length === 0 ? "" : `${label}: ${trimmed.slice(0, 600)}`;
}

function codexMuxTurnText(turn, type) {
  return (turn?.items ?? [])
    .filter((item) => item.type === type)
    .map((item) =>
      item.type === "userMessage"
        ? (item.content ?? [])
            .filter((part) => part.type === "text")
            .map((part) => part.text)
            .join("")
        : (item.text ?? ""),
    )
    .join("\n")
    .trim();
}

function codexMuxForkTitlePrompt(base, context, request) {
  return [
    "You are a helpful assistant. A conversation was forked so the user could take it in a new direction, and your job is to provide a short title for the fork.",
    `The original conversation is titled: ${base}`,
    "The title you generate will be shown in the UI next to the original, so it must say what the fork is about, not repeat the original title.",
    "Generate a concise UI title (up to 36 characters) for the fork.",
    "Fill the structured title field with plain text.",
    "Fill the structured description field with a compact, search-oriented summary (up to 100 characters). Include concrete project names, code areas, artifacts, people, or recurring responsibility terms when relevant so the thread is easy to retrieve by keyword.",
    "Do not include quotes, markdown, formatting characters, or trailing punctuation in either value.",
    "If the request includes a ticket reference (e.g. ABC-123), include it verbatim.",
    "- Use an imperative verb first: \"Add\", \"Fix\", \"Update\", \"Refactor\", \"Remove\", \"Locate\", \"Find\", etc.",
    "- Keep it under 36 characters and under 5 words where possible.",
    "- Base the title on the fork's first request; use the earlier messages only to resolve what it refers to.",
    "- Do NOT respond to the user, answer questions, or attempt to solve the problem; just write a title.",
    "",
    context.length === 0 ? null : "Last messages of the original conversation:",
    context.length === 0 ? null : context,
    context.length === 0 ? null : "",
    "First request in the fork:",
    request,
  ]
    .filter((line) => line != null)
    .join("\n");
}
