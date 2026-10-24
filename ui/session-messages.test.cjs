const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

const sandbox = vm.createContext({
  codexMuxJsx: () => ({ jsx: element, jsxs: element }),
});
function element(type, props) { return { type, props }; }
vm.runInContext(readFileSync(`${__dirname}/session-messages.js`, "utf8"), sandbox);
const parse = sandbox.codexMuxParseSessionMessage;
const render = sandbox.codexMuxSessionMessage;
const body = "Hello **Codex**.\n\n- First check\n- Second check\n\n```html\n<div>text</div>\n```";
const envelope = `<cross-session-message from="uds:/tmp/cc-socks/7070.sock" from-name="Claude: soham revamp" from-mode="prompting">\n${body}\n</cross-session-message>`;

test("recognizes the sessions envelope and preserves markdown and reply address", () => {
  const result = parse(envelope);
  assert.equal(result.name, "Claude: soham revamp");
  assert.equal(result.address, "uds:/tmp/cc-socks/7070.sock");
  assert.equal(result.body, body);
  assert.equal(parse(envelope.replace(' from-mode="prompting"', "")).body, body);
  assert.equal(parse('<cross-session-message from="uds:/a">Hi</cross-session-message>').name, "Another session");
});

test("does not reinterpret quoted examples, partial messages, or combined messages", () => {
  for (const text of [null, "ordinary message", `Example:\n${envelope}`, `\`\`\`xml\n${envelope}\n\`\`\``,
    envelope + "\nMore user instructions", envelope + envelope, envelope.replace("</cross-session-message>", ""),
    envelope.replace(' from="uds:/tmp/cc-socks/7070.sock"', ""),
    envelope.replace(' from-mode="prompting"', ' from="duplicate"'),
    envelope.replace("<cross-session-message", "\\<cross-session-message")]) {
    assert.equal(parse(text), null, String(text));
  }
});

test("decodes sender attributes as text without interpreting body HTML or entities", () => {
  const result = parse(`<cross-session-message from='uds:/a' from-name='Claude &amp; &#x43;odex &quot;&lt;img&gt;&quot;'>A &amp; B</cross-session-message>`);
  assert.equal(result.name, 'Claude & Codex "<img>"');
  assert.equal(result.body, "A &amp; B");
  assert.doesNotThrow(() => parse('<cross-session-message from="uds:/a" from-name="&#9999999999;">Hi</cross-session-message>'));
});

test("uses the native expandable message with distinct sender and left alignment", () => {
  function NativeMessage() {}
  const item = { message: envelope, sentAtMs: 123, images: [], attachments: [] };
  const context = { conversationId: "thread", cwd: "/tmp/project", hostId: "local", compactActions: true };
  const card = render(item, context, NativeMessage);
  const native = card.props.children;
  assert.equal(native.type, NativeMessage);
  assert.equal(native.props.alignment, "start");
  assert.equal(native.props.message, body);
  assert.equal(native.props.sentAtMs, 123);
  assert.equal(native.props.cwd, context.cwd);
  assert.equal(native.props.conversationId, context.conversationId);
  assert.equal(native.props.label.props.title, "uds:/tmp/cc-socks/7070.sock");
  assert.equal(native.props.label.props.children[1].props.children, "Claude: soham revamp");
  assert.equal(item.message, envelope);
  assert.equal(render({ message: "Hi" }, context, NativeMessage), null);
});

test("keeps the native attachment renderer when an envelope includes other content", () => {
  for (const key of ["images", "attachments", "comments", "uploadedFileAttachments", "unavailableFileAttachments",
    "selectedTextAttachments", "responseAnnotations", "appshotContexts", "mcpAppModelContextAttachments"]) {
    assert.equal(render({ message: envelope, [key]: [{}] }, {}, () => {}), null, key);
  }
});
