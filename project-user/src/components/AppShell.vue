<template>
  <!-- 视图层 -->
  <!-- 父组件 -->
  <div class="chat-layout">
    <ChatHistory></ChatHistory>
    <ChatWindow v-if="!project.chatWelcome"></ChatWindow>
    <ChatWelcome v-if="project.chatWelcome"></ChatWelcome>
  </div>
  <ChatInput></ChatInput>
  <Knowledge></Knowledge>
  <UserLogin></UserLogin>
</template>

<script setup lang="ts">
// 逻辑层
import ChatHistory from "./ChatHistory.vue";
import ChatWindow from "./ChatWindow.vue";
import ChatWelcome from "./ChatWelcome.vue";
import ChatInput from "./ChatInput.vue";
import Knowledge from "./Knowledge.vue";
import UserLogin from "./UserLogin.vue";
import { projectStore } from "@/store/index";
const project = projectStore();
</script>

<style>
/* 样式层 */
.chat-layout {
  display: flex;
  height: 100vh;
}
</style>
