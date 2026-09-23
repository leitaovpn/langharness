import readline from "node:readline";
import { stripTypeScriptTypes } from "node:module";

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
    const body = stripTypeScriptTypes(
      `async function __ptc(tools, console) {\n${request.sdk}\n${request.code}\n}`,
      {
      mode: "strip",
      },
    );
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
