// Taro Vue3 dist 在小程序环境下会触发 Vue 运行时对 Node 全局 `process` 的引用，
// 在所有模块加载前注入一个最小 polyfill，避免白屏报错（process is not defined）。
// 仅在缺失时设置，已存在则尊重宿主环境。
if (typeof globalThis.process === "undefined") {
  globalThis.process = { env: { NODE_ENV: "production" } };
}

import { createApp } from "vue";
import { createPinia } from "pinia";
// NutUI 官方样式（此前从未引入，组件以裸元素渲染 = 视觉崩溃的根因）
import "@nutui/nutui-taro/dist/style.css";
import "./app.less";

const App = createApp({
  onShow() {},
});

App.use(createPinia());

export default App;
