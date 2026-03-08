const config = {
  projectName: "ai-quest-miniprogram",
  date: "2026-05-21",
  designWidth: 750,
  deviceRatio: { 640: 2.34, 750: 1, 828: 1.81 },
  sourceRoot: "src",
  outputRoot: "dist",
  framework: "vue3",
  compiler: "vite",
  plugins: [],
  defineConstants: {},
  copy: { patterns: [], options: {} },
  alias: { "@": "./src" },
  mini: {
    postcss: {
      autoprefix: { enable: true },
      pxtransform: { enable: true },
    },
  },
  h5: {},
};

module.exports = function (merge) {
  if (process.env.NODE_ENV === "development") {
    return merge({}, config, require("./dev"));
  }
  return merge({}, config, require("./prod"));
};
