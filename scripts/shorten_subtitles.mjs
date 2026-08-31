import { readFileSync, writeFileSync } from "node:fs";

const [, , inputPath, outputPath] = process.argv;
if (!inputPath || !outputPath) {
  throw new Error("Usage: node scripts/shorten_subtitles.mjs <input.vtt> <output.vtt>");
}

const parseTime = (value) => {
  const [hours, minutes, secondsAndMilliseconds] = value.trim().split(":");
  const [seconds, milliseconds = "0"] = secondsAndMilliseconds.split(/[,.]/);
  return (
    Number(hours) * 3_600_000 +
    Number(minutes) * 60_000 +
    Number(seconds) * 1000 +
    Number(milliseconds.padEnd(3, "0").slice(0, 3))
  );
};

const formatTime = (milliseconds) => {
  const bounded = Math.max(0, Math.round(milliseconds));
  const hours = Math.floor(bounded / 3_600_000);
  const minutes = Math.floor((bounded % 3_600_000) / 60_000);
  const seconds = Math.floor((bounded % 60_000) / 1000);
  const millis = bounded % 1000;
  return [hours, minutes, seconds]
    .map((part) => String(part).padStart(2, "0"))
    .join(":") + `.${String(millis).padStart(3, "0")}`;
};

const splitWords = (text) => {
  const words = text.trim().split(/\s+/);
  const chunks = [];
  let current = [];
  for (const word of words) {
    const candidate = [...current, word].join(" ");
    if (current.length >= 5 || candidate.length > 34) {
      chunks.push(current.join(" "));
      current = [word];
    } else {
      current.push(word);
    }
  }
  if (current.length) chunks.push(current.join(" "));
  if (chunks.length > 1 && chunks.at(-1).split(" ").length === 1) {
    const previousWords = chunks.at(-2).split(" ");
    const lastWord = chunks.at(-1);
    const movedWord = previousWords.at(-1);
    const balancedTail = `${movedWord} ${lastWord}`;
    if (previousWords.length > 1 && balancedTail.length <= 34) {
      chunks[chunks.length - 2] = previousWords.slice(0, -1).join(" ");
      chunks[chunks.length - 1] = balancedTail;
    }
  }
  return chunks;
};

const source = readFileSync(inputPath, "utf8").replace(/^WEBVTT\s*/i, "").trim();
const blocks = source.split(/\r?\n\s*\r?\n/);
const outputCues = [];

for (const block of blocks) {
  const lines = block.split(/\r?\n/).filter(Boolean);
  const timingIndex = lines.findIndex((line) => line.includes("-->"));
  if (timingIndex < 0) continue;
  const [startValue, endValue] = lines[timingIndex].split("-->").map((part) => part.trim());
  const text = lines.slice(timingIndex + 1).join(" ").trim();
  if (!text) continue;

  const start = parseTime(startValue);
  const end = parseTime(endValue);
  const chunks = splitWords(text);
  const totalWeight = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  let cursor = start;
  for (let index = 0; index < chunks.length; index += 1) {
    const chunk = chunks[index];
    const isLast = index === chunks.length - 1;
    const duration = ((end - start) * chunk.length) / totalWeight;
    const chunkEnd = isLast ? end : cursor + duration;
    outputCues.push(`${formatTime(cursor)} --> ${formatTime(chunkEnd)}\n${chunk}`);
    cursor = chunkEnd;
  }
}

writeFileSync(outputPath, `WEBVTT\n\n${outputCues.join("\n\n")}\n`, "utf8");
