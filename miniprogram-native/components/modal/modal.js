/**
 * 轻量自定义弹窗，替代原 NutUI `nut-dialog`（决策一：零 npm 依赖）。
 *
 * 用法：
 *   <x-modal visible="{{show}}" title="标题" bind:close="onClose" bind:confirm="onConfirm">
 *     自定义内容（默认插槽）
 *   </x-modal>
 */
Component({
  properties: {
    visible: { type: Boolean, value: false },
    title: { type: String, value: "" },
    /** 为空则不展示确认按钮（纯信息弹窗） */
    confirmText: { type: String, value: "" },
    cancelText: { type: String, value: "关闭" },
  },

  methods: {
    onClose() {
      this.triggerEvent("close");
    },
    onConfirm() {
      this.triggerEvent("confirm");
    },
    /** 阻断滚动穿透与冒泡（点击弹窗内部不应关闭） */
    noop() {},
  },
});
