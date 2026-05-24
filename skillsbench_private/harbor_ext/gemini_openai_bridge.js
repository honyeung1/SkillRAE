#!/usr/bin/env node

const http = require("node:http");
const { randomUUID } = require("node:crypto");
const { URL } = require("node:url");

const bridgePort = Number.parseInt(
  process.env.SKILLSBENCH_GEMINI_BRIDGE_PORT || "8765",
  10,
);
const upstreamBaseEnv =
  process.env.SKILLSBENCH_GEMINI_UPSTREAM_BASE_URL || "https://api.zyai.online";
const upstreamApiKey =
  process.env.SKILLSBENCH_GEMINI_UPSTREAM_API_KEY ||
  process.env.GOOGLE_API_KEY ||
  process.env.OPENAI_API_KEY ||
  "";
const requestTimeoutMs = Number.parseInt(
  process.env.SKILLSBENCH_GEMINI_UPSTREAM_TIMEOUT_MS || "300000",
  10,
);
const defaultModel =
  process.env.SKILLSBENCH_GEMINI_BRIDGE_DEFAULT_MODEL || "gemini-2.5-pro";
const previewModelAlias =
  process.env.SKILLSBENCH_GEMINI_BRIDGE_PREVIEW_MODEL ||
  "gemini-3-flash-preview-all";
const flashModelAlias =
  process.env.SKILLSBENCH_GEMINI_BRIDGE_FLASH_MODEL || "gemini-2.5-flash";
const webSearchModelAlias =
  process.env.SKILLSBENCH_GEMINI_BRIDGE_WEB_SEARCH_MODEL ||
  previewModelAlias ||
  defaultModel;
const rawAliasEnv = process.env.SKILLSBENCH_GEMINI_BRIDGE_MODEL_ALIASES || "";
const debugEnabled = process.env.SKILLSBENCH_GEMINI_BRIDGE_DEBUG === "1";

function parseAliasMap(rawValue) {
  if (!rawValue || !String(rawValue).trim()) {
    return {};
  }
  try {
    const parsed = JSON.parse(String(rawValue));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    const entries = Object.entries(parsed)
      .filter(
        ([key, value]) =>
          typeof key === "string" &&
          key.trim() &&
          typeof value === "string" &&
          value.trim(),
      )
      .map(([key, value]) => [key.trim(), value.trim()]);
    return Object.fromEntries(entries);
  } catch {
    return {};
  }
}

const explicitModelAliases = parseAliasMap(rawAliasEnv);
const builtinModelAliases = {
  "gemini-3-flash-preview": previewModelAlias,
  "gemini-2.5-flash": flashModelAlias,
  "flash": previewModelAlias,
  "web-search": webSearchModelAlias,
};

function normalizeUpstreamBase(rawBase) {
  const trimmed = String(rawBase || "").trim().replace(/\/+$/, "");
  if (!trimmed) {
    return "https://api.zyai.online/v1";
  }
  if (trimmed.endsWith("/v1")) {
    return trimmed;
  }
  return `${trimmed}/v1`;
}

function normalizeRequestedModel(rawModel) {
  const text = String(rawModel || "").trim();
  if (!text) {
    return defaultModel;
  }
  return text.replace(/^models\//, "");
}

function buildUpstreamModelCandidates(rawModel) {
  const requestedModel = normalizeRequestedModel(rawModel);
  const alias =
    explicitModelAliases[requestedModel] || builtinModelAliases[requestedModel];
  const candidates = [];

  if (alias && alias !== requestedModel) {
    candidates.push(alias);
  }
  candidates.push(requestedModel);
  if (defaultModel && !candidates.includes(defaultModel)) {
    candidates.push(defaultModel);
  }

  return {
    requestedModel,
    candidates,
    aliasApplied: alias && alias !== requestedModel ? alias : null,
  };
}

const upstreamBase = normalizeUpstreamBase(upstreamBaseEnv);
const upstreamChatUrl = `${upstreamBase}/chat/completions`;

function logDebug(message, extra) {
  if (!debugEnabled) {
    return;
  }
  const payload =
    extra === undefined
      ? { ts: new Date().toISOString(), message }
      : { ts: new Date().toISOString(), message, extra };
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function sendJson(res, statusCode, payload) {
  const body = Buffer.from(JSON.stringify(payload), "utf8");
  res.writeHead(statusCode, {
    "Content-Type": "application/json",
    "Content-Length": String(body.length),
    Connection: "close",
  });
  res.end(body);
}

function sendError(res, statusCode, message, details) {
  const payload = {
    error: {
      code: statusCode,
      message,
      status: statusCode >= 500 ? "INTERNAL" : "INVALID_ARGUMENT",
    },
  };
  if (details !== undefined) {
    payload.error.details = details;
  }
  sendJson(res, statusCode, payload);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function stripThinkTags(text) {
  return String(text || "")
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .trim();
}

function summarizePreview(text, maxLength = 200) {
  const value = typeof text === "string" ? text : String(text || "");
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, maxLength)}...`;
}

function safeJsonParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function extractFirstBalancedJsonObjectSpan(text) {
  const source = String(text || "");
  let start = -1;
  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let index = 0; index < source.length; index += 1) {
    const ch = source[index];

    if (start < 0) {
      if (ch === "{") {
        start = index;
        depth = 1;
        inString = false;
        escaped = false;
      }
      continue;
    }

    if (inString) {
      if (escaped) {
        escaped = false;
        continue;
      }
      if (ch === "\\") {
        escaped = true;
        continue;
      }
      if (ch === '"') {
        inString = false;
      }
      continue;
    }

    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === "{") {
      depth += 1;
      continue;
    }
    if (ch === "}") {
      depth -= 1;
      if (depth === 0) {
        const candidate = source.slice(start, index + 1);
        const parsed = safeJsonParse(candidate);
        if (parsed && typeof parsed === "object") {
          return { parsed, start, end: index + 1 };
        }
        start = -1;
      }
    }
  }

  return null;
}

function extractFirstBalancedJsonObject(text) {
  const match = extractFirstBalancedJsonObjectSpan(text);
  return match ? match.parsed : null;
}

function extractJsonObject(text) {
  const direct = safeJsonParse(text);
  if (direct && typeof direct === "object") {
    return direct;
  }

  const fenced = String(text || "")
    .replace(/^```json\s*/i, "")
    .replace(/^```\s*/i, "")
    .replace(/\s*```$/i, "")
    .trim();
  const fencedParsed = safeJsonParse(fenced);
  if (fencedParsed && typeof fencedParsed === "object") {
    return fencedParsed;
  }

  const firstBalanced = extractFirstBalancedJsonObject(fenced);
  if (firstBalanced && typeof firstBalanced === "object") {
    return firstBalanced;
  }

  const start = fenced.indexOf("{");
  const end = fenced.lastIndexOf("}");
  if (start >= 0 && end > start) {
    const candidate = fenced.slice(start, end + 1);
    const parsed = safeJsonParse(candidate);
    if (parsed && typeof parsed === "object") {
      return parsed;
    }
  }

  return null;
}

function shouldParseStructuredToolCallText(text) {
  const cleaned = stripThinkTags(text);
  if (!cleaned) {
    return false;
  }
  return /"type"\s*:\s*"(function_call|tool_call)"/i.test(cleaned)
    || /"function_call"\s*:/i.test(cleaned)
    || /"functionCall"\s*:/i.test(cleaned)
    || /"tool_calls"\s*:/i.test(cleaned);
}

function stripJsonFences(text) {
  const trimmed = String(text || "").trim();
  const fencedMatch = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return fencedMatch ? fencedMatch[1].trim() : trimmed;
}

function isAllowedJsonWrapperText(text) {
  const cleaned = String(text || "").trim();
  if (!cleaned) {
    return true;
  }
  if (/^[:>\-\s`'"()[\]{}*#]+$/.test(cleaned)) {
    return true;
  }
  const compact = cleaned.toLowerCase().replace(/\s+/g, "");
  return (
    compact === "json:"
    || compact === "tool_call:"
    || compact === "function_call:"
    || compact === "response:"
    || compact === "assistant:"
  );
}

function extractStructuredToolCallJson(text) {
  const cleaned = stripThinkTags(text);
  if (!cleaned) {
    return null;
  }
  const normalized = stripJsonFences(cleaned);
  const direct = safeJsonParse(normalized);
  if (direct && typeof direct === "object") {
    return direct;
  }

  const match = extractFirstBalancedJsonObjectSpan(normalized);
  if (!match || typeof match.parsed !== "object") {
    return null;
  }
  const prefix = normalized.slice(0, match.start);
  const suffix = normalized.slice(match.end);
  if (!isAllowedJsonWrapperText(prefix) || !isAllowedJsonWrapperText(suffix)) {
    return null;
  }
  return match.parsed;
}

function flattenTextParts(parts) {
  if (!Array.isArray(parts)) {
    return "";
  }
  return parts
    .map((part) => (typeof part?.text === "string" ? part.text : ""))
    .filter(Boolean)
    .join("");
}

function flattenSystemInstruction(systemInstruction) {
  if (!systemInstruction || typeof systemInstruction !== "object") {
    return "";
  }
  if (Array.isArray(systemInstruction.parts)) {
    return flattenTextParts(systemInstruction.parts);
  }
  if (typeof systemInstruction.text === "string") {
    return systemInstruction.text;
  }
  return "";
}

function extractToolOutput(response) {
  if (!response || typeof response !== "object") {
    return "";
  }
  if (typeof response.output === "string" && response.output.trim()) {
    return response.output.trim();
  }
  try {
    return JSON.stringify(response);
  } catch {
    return String(response);
  }
}

function partToTranscript(part) {
  if (!part || typeof part !== "object") {
    return "";
  }
  if (typeof part.text === "string" && part.text.trim()) {
    return part.text;
  }
  if (part.functionCall) {
    const name = part.functionCall.name || "unknown_tool";
    const args = part.functionCall.args ?? {};
    return `Tool call requested: ${name}\nArguments: ${JSON.stringify(args)}`;
  }
  if (part.functionResponse) {
    const name = part.functionResponse.name || "unknown_tool";
    const output = extractToolOutput(part.functionResponse.response);
    return `Tool result from ${name}:\n${output}`;
  }
  if (part.inlineData) {
    const mime = part.inlineData.mimeType || "application/octet-stream";
    const data = part.inlineData.data || "";
    return `[inlineData ${mime} ${data.length} bytes]`;
  }
  if (part.fileData) {
    const mime = part.fileData.mimeType || "application/octet-stream";
    const uri = part.fileData.fileUri || "";
    return `[fileData ${mime} ${uri}]`;
  }
  return "";
}

function contentToMessage(content) {
  const role = content?.role === "model" ? "assistant" : "user";
  const lines = Array.isArray(content?.parts)
    ? content.parts.map(partToTranscript).filter(Boolean)
    : [];
  return {
    role,
    content: lines.join("\n\n").trim() || "(empty)",
  };
}

function collectToolDeclarations(tools) {
  const declarations = [];
  for (const tool of Array.isArray(tools) ? tools : []) {
    if (!tool || typeof tool !== "object") {
      continue;
    }
    if (!Array.isArray(tool.functionDeclarations)) {
      continue;
    }
    for (const declaration of tool.functionDeclarations) {
      if (!declaration || typeof declaration !== "object") {
        continue;
      }
      const name = declaration.name;
      if (!name || typeof name !== "string") {
        continue;
      }
      declarations.push({
        name,
        description: declaration.description || "",
        parametersJsonSchema: declaration.parametersJsonSchema || {},
      });
    }
  }
  return declarations;
}

function pickBestTextContent(rawContent) {
  if (typeof rawContent === "string") {
    return rawContent;
  }
  if (Array.isArray(rawContent?.parts)) {
    return flattenTextParts(rawContent.parts);
  }
  return "";
}

function extractToolConfig(rawToolConfig) {
  if (!rawToolConfig || typeof rawToolConfig !== "object") {
    return { mode: "", allowedFunctionNames: [] };
  }
  const functionCallingConfig =
    rawToolConfig.functionCallingConfig &&
    typeof rawToolConfig.functionCallingConfig === "object"
      ? rawToolConfig.functionCallingConfig
      : rawToolConfig;
  const mode =
    typeof functionCallingConfig.mode === "string"
      ? functionCallingConfig.mode
      : "";
  const allowedFunctionNames = Array.isArray(
    functionCallingConfig.allowedFunctionNames,
  )
    ? functionCallingConfig.allowedFunctionNames.filter(
        (name) => typeof name === "string" && name,
      )
    : [];
  return { mode, allowedFunctionNames };
}

function buildMessages(model, payload) {
  const toolDeclarations = collectToolDeclarations(payload.tools);
  const { mode, allowedFunctionNames } = extractToolConfig(payload.toolConfig);
  const originalSystemInstruction = flattenSystemInstruction(
    payload.systemInstruction,
  );

  const bridgeDirectives = [
    "You are serving an official Gemini CLI compatibility bridge.",
    "You must return exactly one JSON object and nothing else.",
    'If you need a tool, return {"type":"function_call","name":"tool_name","arguments":{...}}.',
    'If you are ready to answer, return {"type":"final","text":"..."}.',
    "Call at most one tool per response.",
    "Never invent tool outputs. Use tool calls when the answer depends on local files, shell state, or prior tool results.",
    "Do not wrap JSON in markdown fences.",
  ];
  if (mode) {
    bridgeDirectives.push(`Tool selection mode: ${mode}.`);
  }
  if (allowedFunctionNames.length > 0) {
    bridgeDirectives.push(
      `Allowed tool names: ${allowedFunctionNames.join(", ")}.`,
    );
  } else if (toolDeclarations.length > 0) {
    bridgeDirectives.push(
      `Available tool names: ${toolDeclarations.map((tool) => tool.name).join(", ")}.`,
    );
  } else {
    bridgeDirectives.push("No tools are currently available.");
  }

  const systemSections = [bridgeDirectives.join("\n")];
  if (originalSystemInstruction) {
    systemSections.push(
      "Original Gemini system instruction:\n" + originalSystemInstruction,
    );
  }
  if (toolDeclarations.length > 0) {
    systemSections.push(
      "Available tools with schemas:\n" +
        JSON.stringify(toolDeclarations, null, 2),
    );
  }
  systemSections.push(`Requested model: ${model}`);

  const messages = [
    {
      role: "system",
      content: systemSections.join("\n\n"),
    },
  ];

  for (const content of Array.isArray(payload.contents) ? payload.contents : []) {
    messages.push(contentToMessage(content));
  }

  return messages;
}

function parseBridgeDecision(rawText, toolDeclarations) {
  const message =
    rawText && typeof rawText === "object" && !Array.isArray(rawText)
      ? rawText
      : null;
  const cleanText = message ? pickBestTextContent(message.content) : stripThinkTags(rawText);
  const parsed = message ?? extractJsonObject(cleanText);
  const parsedFromMessageContent =
    message &&
    typeof message.content === "string" &&
    shouldParseStructuredToolCallText(message.content)
      ? extractStructuredToolCallJson(message.content)
      : null;
  const knownToolNames = new Set(toolDeclarations.map((tool) => tool.name));

  function normalizeFunctionCall(candidate) {
    if (!candidate || typeof candidate !== "object") {
      return null;
    }

    const type = typeof candidate.type === "string" ? candidate.type.trim().toLowerCase() : "";
    if (type === "tool_call" || type === "function_call") {
      const name = typeof candidate.name === "string" ? candidate.name.trim() : "";
      let args = candidate.arguments ?? candidate.args ?? {};
      if (typeof args === "string") {
        const parsedArgs = safeJsonParse(args);
        args = parsedArgs && typeof parsedArgs === "object" ? parsedArgs : {};
      }
      if (
        name &&
        knownToolNames.has(name) &&
        args &&
        typeof args === "object" &&
        !Array.isArray(args)
      ) {
        return { kind: "function_call", name, arguments: args };
      }
      return null;
    }

    if (
      candidate.function_call &&
      typeof candidate.function_call === "object"
    ) {
      return normalizeFunctionCall(candidate.function_call);
    }

    if (
      candidate.tool_call &&
      typeof candidate.tool_call === "object"
    ) {
      return normalizeFunctionCall(candidate.tool_call);
    }

    if (Array.isArray(candidate.tool_calls) && candidate.tool_calls.length > 0) {
      for (const toolCall of candidate.tool_calls) {
        const normalized = normalizeFunctionCall(
          toolCall.function ?? toolCall.function_call ?? toolCall,
        );
        if (normalized) {
          return normalized;
        }
      }
    }

    if (
      candidate.content &&
      Array.isArray(candidate.content.parts) &&
      candidate.content.parts.length > 0
    ) {
      const functionCallPart = candidate.content.parts.find(
        (part) =>
          part && (part.functionCall || part.function_call || part.tool_call),
      );
      if (functionCallPart) {
        return normalizeFunctionCall(
          functionCallPart.functionCall ||
            functionCallPart.function_call ||
            functionCallPart.tool_call,
        );
      }
    }

    if (Array.isArray(candidate.tool_calls) && candidate.tool_calls.length > 0) {
      for (const toolCall of candidate.tool_calls) {
        const normalized = normalizeFunctionCall(
          toolCall.function ?? toolCall.function_call ?? toolCall,
        );
        if (normalized) {
          return normalized;
        }
      }
    }

    if (candidate.functionCall || candidate.function_call) {
      const normalized = normalizeFunctionCall(
        candidate.functionCall || candidate.function_call,
      );
      if (normalized) {
        return normalized;
      }
    }

    return null;
  }

  if (parsed && typeof parsed === "object") {
    if (Array.isArray(parsed)) {
      for (const callCandidate of parsed) {
        const candidateDecision = normalizeFunctionCall(callCandidate);
        if (candidateDecision) {
          return candidateDecision;
        }
      }
    } else {
      const directDecision = normalizeFunctionCall(parsed);
      if (directDecision) {
        return directDecision;
      }
    }

    const type =
      typeof parsed.type === "string" ? parsed.type.trim().toLowerCase() : "";
    if (type === "function_call" || type === "tool_call") {
      const name =
        typeof parsed.name === "string" ? parsed.name.trim() : "";
      let args = parsed.arguments ?? parsed.args ?? {};
      if (typeof args === "string") {
        const parsedArgs = safeJsonParse(args);
        args = parsedArgs && typeof parsedArgs === "object" ? parsedArgs : {};
      }
      if (
        name &&
        knownToolNames.has(name) &&
        args &&
        typeof args === "object" &&
        !Array.isArray(args)
      ) {
        return {
          kind: "function_call",
          name,
          arguments: args,
        };
      }
    }
    if (Array.isArray(parsed.calls) && parsed.calls.length > 0) {
      const firstCall = parsed.calls[0];
      const name =
        typeof firstCall?.name === "string" ? firstCall.name.trim() : "";
      const args = firstCall?.arguments;
      if (
        name &&
        knownToolNames.has(name) &&
        args &&
        typeof args === "object" &&
        !Array.isArray(args)
      ) {
        return {
          kind: "function_call",
          name,
          arguments: args,
        };
      }
    }
    if (Array.isArray(parsed.tool_calls) && parsed.tool_calls.length > 0) {
      for (const toolCall of parsed.tool_calls) {
        const normalized = normalizeFunctionCall(
          toolCall.function ?? toolCall.function_call ?? toolCall,
        );
        if (normalized) {
          return normalized;
        }
      }
    }
    if (typeof parsed.function_call === "object" && parsed.function_call !== null) {
      const normalized = normalizeFunctionCall(parsed.function_call);
      if (normalized) {
        return normalized;
      }
    }
    if (typeof parsed.functionCall === "object" && parsed.functionCall !== null) {
      const normalized = normalizeFunctionCall(parsed.functionCall);
      if (normalized) {
        return normalized;
      }
    }
    if (parsedFromMessageContent && typeof parsedFromMessageContent === "object") {
      const normalized = normalizeFunctionCall(parsedFromMessageContent);
      if (normalized) {
        return normalized;
      }
    }
    if (
      message &&
      Array.isArray(parsed.tool_calls) === false &&
      Array.isArray(parsed.calls) === false
    ) {
      if (typeof parsed.content === "string" && parsed.content.trim()) {
        const parsedText = stripThinkTags(parsed.content);
        return { kind: "final", text: parsedText || "Done." };
      }
      const partsText = pickBestTextContent(parsed);
      if (partsText) {
        return { kind: "final", text: stripThinkTags(partsText) || "Done." };
      }
    }
    if (typeof parsed.text === "string") {
      return { kind: "final", text: parsed.text };
    }
    if (typeof parsed.content === "string") {
      return { kind: "final", text: parsed.content };
    }
    if (typeof parsed.answer === "string") {
      return { kind: "final", text: parsed.answer };
    }
  }

  return {
    kind: "final",
    text: cleanText || String(rawText || "").trim() || "Done.",
  };
}

async function callUpstream(model, payload) {
  if (!upstreamApiKey) {
    throw new Error("SKILLSBENCH_GEMINI_UPSTREAM_API_KEY is not set");
  }

  const resolution = buildUpstreamModelCandidates(model);
  const toolDeclarations = collectToolDeclarations(payload.tools);
  const messages = buildMessages(model, payload);
  const generationConfig =
    payload.generationConfig && typeof payload.generationConfig === "object"
      ? payload.generationConfig
      : {};
  let lastError = null;

  for (const upstreamModel of resolution.candidates) {
    const upstreamPayload = {
      model: upstreamModel,
      messages,
      stream: false,
      temperature:
        typeof generationConfig.temperature === "number"
          ? generationConfig.temperature
          : 0.2,
      top_p:
        typeof generationConfig.topP === "number"
          ? generationConfig.topP
          : 0.95,
      max_tokens:
        typeof generationConfig.maxOutputTokens === "number"
          ? generationConfig.maxOutputTokens
          : 4096,
    };

    if (toolDeclarations.length === 0) {
      upstreamPayload.response_format = { type: "json_object" };
    }

    logDebug("upstream_request", {
      requestedModel: resolution.requestedModel,
      upstreamModel,
      aliasApplied: resolution.aliasApplied,
      url: upstreamChatUrl,
      messageCount: messages.length,
      toolCount: toolDeclarations.length,
    });

    const abortController = new AbortController();
    const timeout = setTimeout(() => abortController.abort(), requestTimeoutMs);

    let response;
    try {
      response = await fetch(upstreamChatUrl, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${upstreamApiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(upstreamPayload),
        signal: abortController.signal,
      });
    } finally {
      clearTimeout(timeout);
    }

    const rawText = await response.text();
    let parsed;
    try {
      parsed = rawText ? JSON.parse(rawText) : {};
    } catch {
      parsed = {};
    }

    if (!response.ok) {
      lastError = new Error(
        `Upstream request failed with status ${response.status}: ${rawText}`,
      );
      logDebug("upstream_error", {
        requestedModel: resolution.requestedModel,
        upstreamModel,
        aliasApplied: resolution.aliasApplied,
        status: response.status,
        bodyPreview: rawText.slice(0, 500),
      });
      continue;
    }

    const message = parsed?.choices?.[0]?.message;
    const usage =
      parsed?.usage && typeof parsed.usage === "object" ? parsed.usage : {};

    const decision = parseBridgeDecision(message, toolDeclarations);

    if (!decision) {
      lastError = new Error(`Upstream returned no assistant content: ${rawText}`);
      logDebug("upstream_error", {
        requestedModel: resolution.requestedModel,
        upstreamModel,
        aliasApplied: resolution.aliasApplied,
        status: response.status,
        bodyPreview: rawText.slice(0, 500),
      });
      continue;
    }

    const contentPreview =
      typeof message?.content === "string"
        ? message.content
        : pickBestTextContent(message?.content) || "";

    logDebug("upstream_response", {
      requestedModel: resolution.requestedModel,
      upstreamModel,
      aliasApplied: resolution.aliasApplied,
      usage,
      decisionKind: decision.kind,
      decisionName: decision.kind === "function_call" ? decision.name : null,
      contentPreview: summarizePreview(contentPreview),
    });

    return {
      decision,
      usage,
    };
  }

  throw lastError || new Error("Upstream request failed without a specific error");
}

function buildUsageMetadata(usage) {
  const promptTokenCount =
    typeof usage.prompt_tokens === "number" ? usage.prompt_tokens : 0;
  const candidatesTokenCount =
    typeof usage.completion_tokens === "number" ? usage.completion_tokens : 0;
  const totalTokenCount =
    typeof usage.total_tokens === "number"
      ? usage.total_tokens
      : promptTokenCount + candidatesTokenCount;
  return {
    promptTokenCount,
    candidatesTokenCount,
    totalTokenCount,
  };
}

function buildFinalResponse(decision, usage) {
  return {
    candidates: [
      {
        content: {
          role: "model",
          parts: [{ text: decision.text }],
        },
        finishReason: "STOP",
        index: 0,
      },
    ],
    usageMetadata: buildUsageMetadata(usage),
    responseId: randomUUID(),
    modelVersion: "skillsbench-gemini-openai-bridge",
  };
}

function buildFunctionCallResponse(decision, usage) {
  return {
    candidates: [
      {
        content: {
          role: "model",
          parts: [
            {
              functionCall: {
                id: randomUUID(),
                name: decision.name,
                args: decision.arguments,
              },
            },
          ],
        },
        finishReason: "STOP",
        index: 0,
      },
    ],
    usageMetadata: buildUsageMetadata(usage),
    responseId: randomUUID(),
    modelVersion: "skillsbench-gemini-openai-bridge",
  };
}

function sendSseResponse(res, chunks) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "close",
  });
  for (const chunk of chunks) {
    res.write(`data: ${JSON.stringify(chunk)}\n\n`);
  }
  res.end();
}

function routeModelRequest(urlPathname) {
  const match = urlPathname.match(
    /^\/v1beta\/models\/([^:]+):(streamGenerateContent|generateContent)$/,
  );
  if (!match) {
    return null;
  }
  return {
    model: decodeURIComponent(match[1] || defaultModel),
    action: match[2],
  };
}

async function handleGenerate(req, res, route, payload) {
  const { decision, usage } = await callUpstream(route.model, payload);
  if (route.action === "generateContent") {
    if (decision.kind === "function_call") {
      sendJson(res, 200, buildFunctionCallResponse(decision, usage));
      return;
    }
    sendJson(res, 200, buildFinalResponse(decision, usage));
    return;
  }

  if (decision.kind === "function_call") {
    sendSseResponse(res, [buildFunctionCallResponse(decision, usage)]);
    return;
  }
  sendSseResponse(res, [buildFinalResponse(decision, usage)]);
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", "http://127.0.0.1");

    if (req.method === "GET" && url.pathname === "/healthz") {
      sendJson(res, 200, { ok: true });
      return;
    }

    if (req.method === "GET" && url.pathname === "/v1beta/models") {
      sendJson(res, 200, {
        models: [{ name: `models/${defaultModel}` }],
      });
      return;
    }

    const route = routeModelRequest(url.pathname);
    if (req.method === "POST" && route) {
      const rawBody = await readBody(req);
      const payload = rawBody.length > 0 ? JSON.parse(rawBody.toString("utf8")) : {};
      await handleGenerate(req, res, route, payload);
      return;
    }

    sendError(res, 404, `Unsupported path: ${req.method} ${url.pathname}`);
  } catch (error) {
    logDebug("bridge_error", {
      message: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : undefined,
    });
    sendError(
      res,
      500,
      error instanceof Error ? error.message : String(error),
    );
  }
});

server.listen(bridgePort, "127.0.0.1", () => {
  logDebug("bridge_listening", {
    port: bridgePort,
    upstreamBase,
    defaultModel,
  });
});
