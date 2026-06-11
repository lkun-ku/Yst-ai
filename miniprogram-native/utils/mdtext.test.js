import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { parseMd, stripInline } from "./mdtext.js";

describe("stripInline 只剥壳不做富文本", () => {
  it("去掉粗体/行内码/链接的标记，保留文字", () => {
    assert.equal(stripInline("**重点**与`代码`和[链接](http://x)"), "重点与代码和链接");
  });

  it("不误伤单个星号（乘法/脚注常见）", () => {
    assert.equal(stripInline("3 * 4 = 12"), "3 * 4 = 12");
  });
});

describe("parseMd 块级结构", () => {
  it("标题按层级成块", () => {
    const out = parseMd("# 一级\n## 二级");
    assert.deepEqual(out, [
      { type: "h", level: 1, text: "一级" },
      { type: "h", level: 2, text: "二级" },
    ]);
  });

  it("**表格被解析成单元格数组**（这是本次反馈的核心：原文里 | --- | 裸露得像坏数据）", () => {
    const md = "| 特性 | Vue3 |\n| --- | --- |\n| 响应式 | Proxy |\n| 打包 | Vite |";
    const out = parseMd(md);
    assert.equal(out.length, 1);
    assert.equal(out[0].type, "table");
    assert.deepEqual(out[0].rows, [
      ["特性", "Vue3"],
      ["响应式", "Proxy"],
      ["打包", "Vite"],
    ]);
  });

  it("无序与有序列表分别标记", () => {
    const out = parseMd("- 甲\n- 乙\n1. 丙");
    assert.deepEqual(out, [
      { type: "li", text: "甲", ordered: false },
      { type: "li", text: "乙", ordered: false },
      { type: "li", text: "丙", ordered: true },
    ]);
  });

  it("连续普通行合并成一段（不逐行断开）", () => {
    const out = parseMd("第一句。\n第二句。\n\n第三句。");
    assert.deepEqual(out, [
      { type: "p", text: "第一句。 第二句。" },
      { type: "p", text: "第三句。" },
    ]);
  });

  it("代码块内部原样保留，不做任何解析", () => {
    const out = parseMd("```js\nconst a = 1; // | 不是表格 |\n```");
    assert.equal(out.length, 1);
    assert.equal(out[0].type, "code");
    assert.equal(out[0].text, "const a = 1; // | 不是表格 |");
  });

  it("空输入返回空数组而不是抛错", () => {
    assert.deepEqual(parseMd(""), []);
    assert.deepEqual(parseMd(null), []);
  });
});
