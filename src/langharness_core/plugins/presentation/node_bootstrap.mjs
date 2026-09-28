import readline from "node:readline";

const input = readline.createInterface({ input: process.stdin });
const pending = new Map();
let sequence = 0;
let request = null;

function send(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

input.on("line", (line) => {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    return;
  }
  if (message.type === "start") {
    request = message;
    void execute();
  } else if (message.type === "tool_result") {
    const resolve = pending.get(message.id);
    if (resolve) {
      pending.delete(message.id);
      if (message.error) {
        const error = new Error(message.error.message ?? "Tool call failed");
        error.name = "ToolCallError";
        error.code = message.error.code ?? "tool_call_failed";
        resolve.reject(error);
      } else {
        resolve.resolve(message.result);
      }
    }
  }
});

function callTool(name, args) {
  const id = String(++sequence);
  send({ type: "tool_call", id, name, args });
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

// The SDK is declaration-only and is intentionally not evaluated.  Keep the
// runtime transform dependency-free: Node's stripTypeScriptTypes uses a WASM
// compiler which cannot initialize after the PTC address-space limit is set.
// This narrow transform covers the generated PTC syntax (variable/parameter
// annotations and `as` assertions) without touching object-literal colons.
function stripRuntimeTypes(source) {
  return source
    .replace(/([A-Za-z_$][\w$]*)\s*:\s*[A-Za-z_$][\w$]*(?=\s*[=;,)])/g, "$1")
    .replace(/\s+as\s+[A-Za-z_$][\w$]*(?=\s*[,;)\]}])/g, "");
}

async function execute() {
  const logs = [];
  const tools = new Proxy(Object.create(null), {
    get(_target, name) {
      if (typeof name !== "string" || name === "run_code") return undefined;
      return (args) => callTool(name, args);
    },
  });
  const safeConsole = {
    log: (...values) => logs.push(values.map(String).join(" ")),
    info: (...values) => logs.push(values.map(String).join(" ")),
    warn: (...values) => logs.push(values.map(String).join(" ")),
    error: (...values) => logs.push(values.map(String).join(" ")),
  };
  try {
    const body = `async function __ptc(tools, console) {\n${stripRuntimeTypes(request.code)}\n}`;
    const run = new Function(`${body}; return __ptc;`)();
    const result = await run(tools, safeConsole);
    send({ type: "done", result, logs });
  } catch (error) {
    send({
      type: "done",
      error: { name: error?.name ?? "Error", message: error?.message ?? String(error) },
      logs,
    });
  }
}
