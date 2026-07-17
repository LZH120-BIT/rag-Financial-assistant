<template>
  <!-- 左侧边栏：消息列表 -->
  <div class="chat-history-view">
    <div class="new-dialog">
      <el-button type="primary" :disabled="project.disabledStatus" @click="createSession">新建对话</el-button>
    </div>
    <div style="height: 80px"></div>
    <div
      class="dialog-list"
      v-for="(item, index) in project.chatListData"
      :key="index"
      @click="handleSessionClick(index, item.sessionId)"
      :style="{ backgroundColor: index === project.sessionIndex ? '#f3f2ff' : '' }"
    >
      <div class="dialog-list-item hidden-text" :style="{ color: index === project.sessionIndex ? '#615ced' : '' }">{{ item.content }}</div>
      <!-- 删除会话按钮：hover时显示 -->
      <el-icon class="delete-session" @click.stop="deleteChat(index, item.sessionId)"><Delete /></el-icon>
    </div>
    <!-- 个人信息 -->
    <div class="user-profile">
      <div class="avatar-username" v-if="project.userInfo">
        <img :src="project.userInfo.avatar" alt="" />
        <span>{{ project.userInfo.phoneNumber }}</span>
      </div>
      <el-button v-else type="primary" size="default" @click="project.showLoginPopup = true">登录</el-button>
      <el-button type="primary" size="default" v-if="project.userInfo" @click="project.knowledgePopup = true">知识库管理</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { projectStore } from "@/store/index";
const project = projectStore();
import { GetChatListApi, DeleteChatApi } from "@/api/request";
import { onMounted } from "vue";
import { getItemChat } from "@/api/getItemChat";
import { Delete } from "@element-plus/icons-vue";
// 逻辑层
onMounted(async () => {
  const res = await GetChatListApi();
  console.log(res);
  project.chatListData = res.data;
  // 获取当前对话下的数据
  if (project.sessionId !== "null") {
    await getItemChat(project.sessionIndex, project.sessionId);
  }
});
// 点击每个会话
const handleSessionClick = async (index: number, sessionId: string) => {
  if (project.disabledStatus) return false;
  await getItemChat(index, sessionId);
};
// 新建对话
const createSession = () => {
  project.chatWelcome = true;
  project.getSessIonIndex(-1);
  project.getSessionId("null");
  project.messageList = [];
};
// 删除某个会话
const deleteChat = async (index: number, sessionId: string) => {
  // 模型正在输出时禁止删除
  if (project.disabledStatus) return false;
  // 二次确认弹窗
  try {
    await ElMessageBox.confirm("确定要删除这个对话吗？删除后无法恢复", "提示", {
      confirmButtonText: "确定",
      cancelButtonText: "取消",
      type: "warning",
    });
  } catch {
    // 用户点了取消，直接返回
    return false;
  }
  // loading 提示
  const loading = ElLoading.service({
    lock: true,
    text: "删除中...",
    background: "rgba(0, 0, 0, 0.8)",
  });
  try {
    // 调用后端删除接口
    await DeleteChatApi({ sessionId });
    // 从列表里移除该项
    project.chatListData.splice(index, 1);
    // 如果删除的是当前正在查看的会话，重置回欢迎页
    if (project.sessionId === sessionId) {
      createSession();
    } else if (index < project.sessionIndex) {
      // 如果删除的会话在当前会话之前，当前会话的下标要前移一位
      project.getSessIonIndex(project.sessionIndex - 1);
    }
    loading.close();
    ElMessage.success("删除成功");
  } catch (error) {
    loading.close();
    ElMessage.error("删除失败");
  }
};
</script>

<style scoped>
/* 样式层 */
.chat-history-view {
  background-color: #ffffff;
  width: 230px;
  position: fixed;
  left: 0;
  top: 0;
  bottom: 0;
  overflow-y: auto;
}
.new-dialog {
  position: fixed;
  top: 0;
  left: 0;
  width: 230px;
  height: 80px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.dialog-list {
  margin: 10px;
  padding: 8px;
  border-radius: 8px;
  position: relative;
  display: flex;
  align-items: center;
}
.dialog-list:hover {
  background-color: #f3f2ff;
  cursor: pointer;
}
.dialog-list:hover .dialog-list-item {
  color: #615ced;
}
.dialog-list .dialog-list-item {
  flex: 1;
  min-width: 0;
}
/* 删除会话图标：默认隐藏，hover时显示 */
.delete-session {
  opacity: 0;
  color: #999;
  margin-left: 6px;
  flex-shrink: 0;
  transition: opacity 0.2s, color 0.2s;
}
.dialog-list:hover .delete-session {
  opacity: 1;
}
.delete-session:hover {
  color: #f56c6c;
}
.user-profile {
  position: fixed;
  bottom: 0;
  left: 0;
  width: 230px;
  height: 120px;
  background-color: #ffffff;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.avatar-username {
  display: flex;
  align-items: center;
  padding-bottom: 20px;
}
.avatar-username img {
  width: 30px;
  height: 30px;
  object-fit: cover;
  border-radius: 50%;
  margin-right: 7px;
}
</style>
