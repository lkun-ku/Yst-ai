/**
 * 极简 Markdown → 结构化块。**只做"能读"所需的那一层**，不做完整渲染。
 *
 * ## 为什么需要它（2026-06-15 实测反馈）
 *
 * 用户传的资料多为 `.md`，而原文里的表格/标题在 `<text>` 里**原样显示**就是一堆
 * `| --- | --- |`、`##`、`**` —— 反馈原话是"用户看切片没意义"。问题不在"有没有原文"，
 * 而在**原文没有被当作文档读**。所以这里把 md 拆成块，交给 WXML 用样式渲染。
 *
 * ## 三条刻意的取舍
 *
 * 1. **不引入 md 渲染库**：小程序端多一个依赖就多一份包体与风险，而我们要的只是
 *    "标题像标题、表格像表格、列表像列表"。
 * 2. **行内标记（`**b**`、`` `c` ``）只剥标记、不做出富文本**：小程序要真加粗得走
 *    `rich-text`，那会引入 XSS 面与调色成本；这里只保证**读起来不夹带语法垃圾**。
 * 3. **表格按"单元格数组"给结构**，由 WXML 决定怎么摆 —— 分离数据与呈现，
 *    也让这块逻辑可以纯函数单测（见 `mdtext.test.js`）。
 */

const _HEADING = /^(#{1,6})\s+(.*)$/;
const _BULLET = /^[-*+]\s+(.*)$/;
const _ORDERED = /^\d+[.、)]\s+(.*)$/;
const _TABLE_ROW = /^\s*\|.*\|\s*$/;
const _TABLE_SEP = /^\s*\|[\s:|-]+\|\s*$/;
const _FENCE = /^\s*```(\w*)\s*$/;
const _QUOTE = /^\s*>\s?(.*)$/;

/** 行内标记只剥壳：`**粗**` → `粗`，`` `码` `` → `码`，`[文](链)` → `文`。 */
export function stripInline(text) {
  return String(text || "")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1$2")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/~~([^~]+)~~/g, "$1")
    .trim();
}

function _cells(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => stripInline(c));
}

/**
 * 把 markdown 文本拆成块数组。
 * 返回项：`{type:"h",level,text}` / `{type:"p",text}` / `{type:"li",text,ordered}` /
 * `{type:"table",rows}` / `{type:"code",text}`。
 */
export function parseMd(text) {
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let para = [];

  const flushPara = () => {
    if (para.length) {
      blocks.push({ type: "p", text: para.join(" ").trim() });
      para = [];
    }
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];

    // 代码围栏：里面的内容**不做任何解析**（原样保留缩进与符号）
    const fence = line.match(_FENCE);
    if (fence) {
      flushPara();
      const buf = [];
      i += 1;
      while (i < lines.length && !_FENCE.test(lines[i])) {
        buf.push(lines[i]);
        i += 1;
      }
      blocks.push({ type: "code", text: buf.join("\n") });
      continue;
    }

    if (!line.trim()) {
      flushPara();
      continue;
    }

    // 表格：当前行是表行且下一行是分隔行 → 收整张表
    if (_TABLE_ROW.test(line) && _TABLE_SEP.test(lines[i + 1] || "")) {
      flushPara();
      const rows = [_cells(line)];
      i += 2;
      while (i < lines.length && _TABLE_ROW.test(lines[i])) {
        rows.push(_cells(lines[i]));
        i += 1;
      }
      i -= 1;
      blocks.push({ type: "table", rows });
      continue;
    }

    const h = line.match(_HEADING);
    if (h) {
      flushPara();
      blocks.push({ type: "h", level: h[1].length, text: stripInline(h[2]) });
      continue;
    }

    const b = line.match(_BULLET);
    const o = b ? null : line.match(_ORDERED);
    if (b || o) {
      flushPara();
      blocks.push({ type: "li", text: stripInline((b || o)[1]), ordered: !!o });
      continue;
    }

    const q = line.match(_QUOTE);
    if (q) {
      flushPara();
      blocks.push({ type: "p", text: `「${stripInline(q[1])}」` });
      continue;
    }

    para.push(stripInline(line));
  }
  flushPara();
  return blocks;
}
