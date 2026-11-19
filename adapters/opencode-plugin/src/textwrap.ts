/**
 * 终端文本的宽度计算与折行：树内标题尾部省略、详情叙事软换行共用同一套宽度口径。
 *
 * 上游：panel.tsx / detailpane.tsx / session.ts。
 * 下游：只导出纯函数，不触碰 IO 与宿主 API。
 */

/** 单字符的显示宽度：CJK 全角算 2 列，组合记号算 0 列，其余算 1 列。 */
export function charWidth(char: string): number {
  const code = char.codePointAt(0) ?? 0;
  if (code === 0 || code < 32 || (code >= 0x7f && code < 0xa0)) return 0;
  // 组合记号与零宽字符。
  if (
    (code >= 0x0300 && code <= 0x036f) ||
    (code >= 0x200b && code <= 0x200f) ||
    (code >= 0xfe00 && code <= 0xfe0f) ||
    (code >= 0xfe20 && code <= 0xfe2f)
  ) {
    return 0;
  }
  // 全角与宽字符区间。
  if (
    (code >= 0x1100 && code <= 0x115f) ||
    (code >= 0x2e80 && code <= 0xa4cf) ||
    (code >= 0xac00 && code <= 0xd7a3) ||
    (code >= 0xf900 && code <= 0xfaff) ||
    (code >= 0xfe30 && code <= 0xfe6f) ||
    (code >= 0xff00 && code <= 0xff60) ||
    (code >= 0xffe0 && code <= 0xffe6) ||
    (code >= 0x20000 && code <= 0x3fffd)
  ) {
    return 2;
  }
  return 1;
}

/** 字符串的显示宽度（列数）。 */
export function displayWidth(text: string): number {
  let width = 0;
  for (const char of text) width += charWidth(char);
  return width;
}

/** 尾部省略：保留开头，超出列数的末尾裁掉并加「…」（与前部省略相反）。 */
export function clipTail(text: string, maxWidth: number): string {
  if (maxWidth <= 0) return "";
  if (displayWidth(text) <= maxWidth) return text;
  const budget = maxWidth - 1;
  const chars = Array.from(text);
  const kept: string[] = [];
  let width = 0;
  for (const char of chars) {
    const charWidthValue = charWidth(char);
    if (width + charWidthValue > budget) break;
    kept.push(char);
    width += charWidthValue;
  }
  return `${kept.join("")}…`;
}

/** 单行软换行：在列宽内优先于空白处断行，超长单词按字符硬断。 */
function wrapLine(line: string, width: number): string[] {
  const result: string[] = [];
  let rest = line;
  while (displayWidth(rest) > width) {
    const chars = Array.from(rest);
    let used = 0;
    let cut = 0;
    let lastBlank = -1;
    for (let index = 0; index < chars.length; index++) {
      const charWidthValue = charWidth(chars[index]);
      if (used + charWidthValue > width) break;
      used += charWidthValue;
      cut = index + 1;
      if (/\s/.test(chars[index])) lastBlank = index + 1;
    }
    if (cut === 0) cut = 1;
    let split = lastBlank > 0 && lastBlank < cut ? lastBlank : cut;
    let head = chars.slice(0, split).join("").replace(/\s+$/, "");
    if (head === "") {
      split = cut;
      head = chars.slice(0, split).join("");
    }
    result.push(head);
    rest = chars.slice(split).join("").replace(/^\s+/, "");
  }
  result.push(rest);
  return result;
}

/**
 * 详情区叙事文本的固定开销（列）：详情框边框 2 + 内边距 1 + 详情右边距 1 + 条目右边距 1 + 行号前缀 7。
 * 叙事可用宽度 = 详情框宽度 - 本值。
 */
export const NARRATIVE_CHROME_COLUMNS = 12;

/** 叙事正文可用列数（按详情框实际宽度推算）。 */
export function narrativeTextWidth(containerWidth: number): number {
  return Math.max(8, Math.floor(containerWidth) - NARRATIVE_CHROME_COLUMNS);
}

/**
 * 叙事软换行：按可用列宽把多行叙事拆成展示行，保留原文换行（空行留空）。
 * 分页以折行后的行数为准，因此长段落能靠翻页看完。
 */
export function wrapNarrative(narrative: string, width: number): string[] {
  const limit = Math.max(1, Math.floor(width));
  const lines: string[] = [];
  for (const raw of (narrative ?? "").split("\n")) {
    if (raw.trim() === "") {
      lines.push("");
      continue;
    }
    lines.push(...wrapLine(raw, limit));
  }
  return lines.length > 0 ? lines : [""];
}

/** 叙事 + 小节共用行序列的一行：kind 决定是否是小节标题（渲染用主色）。 */
export interface NarrativeDisplayLine {
  text: string;
  kind: "body" | "sectionTitle";
}

/** 小节的最小结构：标题与正文（与 LogicSection 同形，便于纯函数层不依赖业务类型）。 */
export interface NarrativeSection {
  title: string;
  body: string;
}

/**
 * 叙事与机制小节拼成同一条展示行序列：小节标题独占一行（渲染成主色），小节正文按宽度软换行。
 * 叙事与小节共享这一条序列，分页行数按整条序列算，长叙事与最后一节都能翻到。
 */
export function wrapNarrativeWithSections(
  narrative: string,
  sections: readonly NarrativeSection[] | undefined,
  width: number,
): NarrativeDisplayLine[] {
  const limit = Math.max(1, Math.floor(width));
  const lines: NarrativeDisplayLine[] = wrapNarrative(narrative, limit).map((text) => ({
    text,
    kind: "body",
  }));
  for (const section of sections ?? []) {
    lines.push({ text: section.title ?? "", kind: "sectionTitle" });
    for (const text of wrapNarrative(section.body ?? "", limit)) {
      lines.push({ text, kind: "body" });
    }
  }
  return lines;
}
