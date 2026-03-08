import { defineStore } from "pinia";

export const useSessionStore = defineStore("session", {
  state: () => ({
    sessionId: 0,
    questions: [] as Record<string, any>[],
    module: "",
    knowledgePoint: "",
  }),
  actions: {
    setSession(payload: {
      session_id: number;
      questions: Record<string, any>[];
      module?: string;
      knowledge_point?: string;
    }) {
      this.sessionId = payload.session_id;
      this.questions = payload.questions;
      this.module = payload.module || "";
      this.knowledgePoint = payload.knowledge_point || "";
    },
    clear() {
      this.sessionId = 0;
      this.questions = [];
      this.module = "";
      this.knowledgePoint = "";
    },
  },
});
