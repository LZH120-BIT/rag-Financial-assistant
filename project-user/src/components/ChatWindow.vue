<template>
  <!-- 消息界面 -->
  <div class="chat-window">
    <div class="chat-message" v-for="(item, index) in project.messageList" :key="index">
      <!-- 用户消息 -->
      <template v-if="item.role == 'user'">
        <div class="user-message">
          <p>{{ item.displayContent || item.content }}</p>
        </div>
        <div class="file-view">
          <div class="file-item" v-for="(itema, indexa) in item.uploadFileList" :key="indexa">
            <div class="file-icon"><img :src="itema.fileType == 'PDF' ? pdfIcon : docxIcon" alt="" /></div>
            <div class="file-name">
              <span class="hidden-text hidden-text">{{ itema.fileName }}</span>
              <span>{{ itema.fileSize }}</span>
            </div>
          </div>
        </div>
      </template>
      <!-- 模型消息 -->
      <div class="ai-message" v-if="item.role == 'assistant'">
        <el-collapse v-if="item.readFileData">
          <el-collapse-item :title="item.readFileData.promptInfo">
            <div v-for="(itemb, indexb) in item.readFileData.fileList" :key="indexb">{{ indexb + 1 + "." }}{{ itemb }}</div>
          </el-collapse-item>
        </el-collapse>
        <div v-html="marked(item.content)"></div>
        <!-- loading -->
        <div class="loading-circle" v-if="item.loadingCircle"></div>
      </div>
    </div>
    <div style="height: 280px"></div>
  </div>
</template>

<script setup lang="ts">
// 逻辑层
// import { ref } from "vue";
import { projectStore } from "@/store/index";
const project = projectStore();
// 引入图标
import docxIcon from "@/assets/docx-icon.png";
import pdfIcon from "@/assets/pdf-icon.png";
import { marked } from "marked";
</script>

<style scoped>
/* 样式层 */
.chat-window {
  margin-left: 230px;
  width: 100%;
}
.chat-message {
  display: flex;
  flex-direction: column;
  max-width: 1000px;
  margin: 0 auto;
  overflow: hidden;
}
.user-message {
  margin-top: 15px;
  max-width: 70%;
  align-self: flex-end;
}
.user-message p {
  line-height: 1.5;
  background-color: #3a71e8;
  border-radius: 10px;
  color: #ffffff;
  padding: 10px;
}
.file-view {
  display: flex;
  align-items: center;
  align-self: flex-end;
  flex-flow: wrap;
  margin-top: 15px;
}
.file-item {
  display: inline-flex;
  border: 1px solid #f3f3f3;
  padding: 5px;
  border-radius: 10px;
  /* 子元素在末端对其 */
  align-self: flex-end;
  background-color: #ffffff;
  max-width: 270px;
  margin-left: 5px;
}
.file-item img {
  width: 30px;
  height: 30px;
}
.file-icon {
  display: flex;
  align-self: center;
}
.file-name {
  display: flex;
  flex-direction: column;
  padding-left: 10px;
}
.file-name span:nth-child(1) {
  font-size: 14px;
}
.file-name span:nth-child(2) {
  font-size: 12px;
  color: #8d8ea5;
}
.ai-message {
  margin-top: 15px;
  background-color: #ffffff;
  padding: 10px;
  border-radius: 10px;
}
.ai-message >>> .el-collapse-item__header {
  color: blue;
  font-size: 15px;
}
.ai-message >>> .el-collapse-item__content {
  background-color: #f7f8fc;
  padding-bottom: 0 !important;
  padding-left: 5px;
}
.ai-message >>> .el-collapse {
  margin-bottom: 10px;
}
.loading-circle {
  width: 12px;
  height: 12px;
  background-color: #3a71e8;
  border-radius: 50%;
  margin: 5px 0;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.3);
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse {
  0%,
  100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.5);
    opacity: 0.7;
  }
}
</style>
