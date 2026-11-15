// Pi extension loaded by `tentacles` for a `type: browser` cold start (#82).
//
// The controlled `pi --mode rpc` process starts with discovery and every
// built-in tool disabled, so these are the only tools the Agent can call. Each
// one forwards to the loopback bridge the fetching process runs over one work
// tab; the bridge executes it, records the trajectory step and answers with the
// observation. Nothing here touches the shell, the filesystem or a browser
// handle, and the bridge URL and token come from the environment of the run.
import { Type } from "typebox";

const bridgeUrl = process.env.TENTACLES_BROWSER_BRIDGE_URL ?? "";
const bridgeToken = process.env.TENTACLES_BROWSER_BRIDGE_TOKEN ?? "";
const MAX_TEXT = 12000;

async function callBridge(tool, args) {
  if (!bridgeUrl) {
    throw new Error("no browser bridge is configured for this session");
  }
  const response = await fetch(bridgeUrl, {
    method: "POST",
    headers: { "content-type": "application/json", "x-tentacles-bridge": bridgeToken },
    body: JSON.stringify({ tool, args }),
  });
  const raw = await response.text();
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    throw new Error(`browser bridge answered ${response.status}: ${raw.slice(0, 200)}`);
  }
  const text = JSON.stringify(payload);
  return {
    content: [{ type: "text", text: text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}…` : text }],
    details: payload,
    isError: payload?.ok === false,
  };
}

const rationale = () =>
  Type.Optional(
    Type.String({
      description: "One sentence on why this action is needed for the collection task.",
    }),
  );

export default function (pi) {
  pi.registerTool({
    name: "browser_navigate",
    label: "Browser Navigate",
    description: "Open an http(s) URL in the controlled browser work tab and wait for the document.",
    promptGuidelines: [
      "Use browser_navigate to open the entry page before reading anything from it.",
    ],
    parameters: Type.Object({ url: Type.String({ description: "Absolute http(s) URL." }), rationale: rationale() }),
    async execute(_toolCallId, params) {
      return callBridge("navigate", params);
    },
  });

  pi.registerTool({
    name: "browser_snapshot",
    label: "Browser Snapshot",
    description:
      "Read the current page: URL, title, a compact accessibility tree and the structural selector candidates it offers.",
    parameters: Type.Object({ rationale: rationale() }),
    async execute(_toolCallId, params) {
      return callBridge("snapshot", params);
    },
  });

  pi.registerTool({
    name: "browser_click",
    label: "Browser Click",
    description: "Click the first element matching a CSS selector in the current page.",
    parameters: Type.Object({
      selector: Type.String({ description: "CSS selector of the element to click." }),
      rationale: rationale(),
    }),
    async execute(_toolCallId, params) {
      return callBridge("click", params);
    },
  });

  pi.registerTool({
    name: "browser_fill",
    label: "Browser Fill",
    description: "Type a value into the first element matching a CSS selector, optionally pressing Enter.",
    parameters: Type.Object({
      selector: Type.String({ description: "CSS selector of the input to fill." }),
      value: Type.String({ description: "Text to type into the field." }),
      submit: Type.Optional(Type.Boolean({ description: "Press Enter after typing." })),
      rationale: rationale(),
    }),
    async execute(_toolCallId, params) {
      return callBridge("fill", params);
    },
  });

  pi.registerTool({
    name: "browser_scroll",
    label: "Browser Scroll",
    description: "Scroll the current page by a bounded number of pixels.",
    parameters: Type.Object({
      direction: Type.Optional(Type.String({ description: '"down" (default) or "up".' })),
      amount: Type.Optional(Type.Number({ description: "Pixels to scroll, at most 5000." })),
      rationale: rationale(),
    }),
    async execute(_toolCallId, params) {
      return callBridge("scroll", params);
    },
  });

  pi.registerTool({
    name: "browser_evaluate",
    label: "Browser Evaluate",
    description:
      "Run one JavaScript expression in the current page and return its JSON result; the main way to read many entries at once.",
    promptGuidelines: [
      "Use browser_evaluate with one expression that returns plain JSON (arrays/objects of strings and numbers).",
    ],
    parameters: Type.Object({
      code: Type.String({ description: "JavaScript expression, e.g. `() => Array.from(...)`." }),
      rationale: rationale(),
    }),
    async execute(_toolCallId, params) {
      return callBridge("evaluate", params);
    },
  });

  pi.registerTool({
    name: "browser_submit_result",
    label: "Browser Submit Result",
    description:
      "Finish the collection task: submit the entries you extracted so they can seed the fixed replay script.",
    promptGuidelines: [
      "Use browser_submit_result exactly once, after the entries are extracted, and then stop.",
    ],
    parameters: Type.Object({
      items: Type.Array(
        Type.Object({
          title: Type.String({ description: "Entry title, required and non-empty." }),
          url: Type.Optional(Type.String({ description: "Entry link, absolute or relative to the page." })),
          author: Type.Optional(Type.String()),
          summary: Type.Optional(Type.String()),
          published: Type.Optional(Type.String({ description: "ISO 8601 timestamp when the page shows one." })),
          comments_url: Type.Optional(Type.String()),
        }),
        { description: "The entries you extracted from the page." },
      ),
      page_url: Type.Optional(Type.String({ description: "Entry page URL the items were read from." })),
      rationale: rationale(),
    }),
    async execute(_toolCallId, params) {
      return callBridge("submit_result", params);
    },
  });
}
