<template>
  <!-- 底部输入框 -->
  <div class="chat-input">
    <div class="chat-input-flex">
      <!-- 查询知识库的按钮 -->
      <el-button class="query-kb-button" @click="queryKb" v-if="uploadFileItem.length <= 0">
        <div class="query-kb">
          <img src="../assets/zhishiku.png" alt="" />
          <span>知识库问答</span>
        </div>
      </el-button>
      <!-- 文件上传的列表 -->
      <div class="upload-file-list" v-if="uploadFileItem.length > 0">
        <div class="upload-file-item" v-for="(item, index) in uploadFileItem" :key="index">
          <img :src="item.fileType == 'PDF' ? pdfIcon : docxIcon" alt="" />
          <div>
            <span class="hidden-text">{{ item.fileName }}</span>
            <span>{{ item.fileSize }}</span>
          </div>
          <el-icon :size="11" class="delete-file" @click="deleteFile(item.docId, index)"><CloseBold /></el-icon>
        </div>
      </div>
      <!-- 输入框 -->
      <div class="chat-input-content">
        <input type="file" multiple :accept="uploadFileType" style="display: none" @change="handleFileChange" ref="fileInputRef" />
        <!-- 财报专用的隐藏 input（接受图片+PDF，单选） -->
        <input type="file" :accept="reportFileType" style="display: none" @change="handleReportChange" ref="reportInputRef" />
        <!-- 上传入口：下拉菜单（本地上传 / 从知识库选择 / 上传财报快速解读） -->
        <el-popover placement="top-start" :width="170" trigger="click" popper-class="upload-menu-popper">
          <template #reference>
            <div class="upload-icon">
              <img src="../assets//upload-icon.png" alt="" />
            </div>
          </template>
          <div class="upload-menu">
            <div class="upload-menu-item" @click="triggerFileInput">
              <el-icon><Upload /></el-icon>
              <span>上传本地文件</span>
            </div>
            <div class="upload-menu-item" @click="openKbSelect">
              <el-icon><Folder /></el-icon>
              <span>从知识库选择</span>
            </div>
            <div class="upload-menu-item" @click="triggerReportInput">
              <el-icon><Document /></el-icon>
              <span>上传财报快速解读</span>
            </div>
          </div>
        </el-popover>
        <el-input
          v-model="userMessage"
          type="textarea"
          placeholder="任何财报相关问题都可以问我 · Ask me anything about financial reports (Shift + Enter 换行)"
          :autosize="{ minRows: 1, maxRows: 4 }"
          resize="none"
          @keydown="handleKeyDown"
        />
        <el-button v-if="!project.disabledStatus">
          <img src="../assets//send-icon.png" alt="" class="send-icon" @click="sendMessage" />
        </el-button>
        <el-button v-if="project.disabledStatus">
          <img src="../assets/stop-icon.png" alt="" class="send-icon" @click="stopOutput" />
        </el-button>
      </div>
    </div>
    <!-- 从知识库选择文件的弹窗 -->
    <KbFileSelect v-model="kbSelectVisible" @select="handleKbFileSelect" />
  </div>
</template>

<script setup lang="ts">
// 逻辑层
import { CloseBold, Upload, Folder, Document } from "@element-plus/icons-vue";
import { reactive, ref } from "vue";
import KbFileSelect from "./KbFileSelect.vue";
import type { KbFileListType } from "@/types/index";
// 财报允许的文件类型：图片 + PDF + DOCX + XLSX（用扩展名 accept，比 mimetype 兼容性好）
const reportFileType = ".jpg,.jpeg,.png,.webp,.bmp,.tiff,.tif,.pdf,.docx,.xlsx";
// 文件上传的类型
const uploadFileType = "application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
// 输入的内容
const userMessage = ref("");
//#region
// 键盘事件
const handleKeyDown = (event: KeyboardEvent) => {
  // 阻止换行
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
};
const queryKbStyle = reactive({
  border: "#eceff3",
  backgroundColor: "#ffffff",
  color: "#d783af",
});
const isKnowledgeBased = ref(false);
// 点击知识库按钮
const queryKb = () => {
  isKnowledgeBased.value = !isKnowledgeBased.value;
  if (isKnowledgeBased.value) {
    // 选中
    queryKbStyle.backgroundColor = "#597CEE";
    queryKbStyle.border = "#597CEE";
    queryKbStyle.color = "#ffffff";
  } else {
    queryKbStyle.backgroundColor = "#ffffff";
    queryKbStyle.border = "#eceff3";
    queryKbStyle.color = "#d783af";
  }
};
//#endregion
// 文件上传
import { UploadDialogApi, DeleteFileApi, SendMessageApi, StopOutputApi, UploadReportApi } from "@/api/request";
import { useFileUploader } from "@/api/useFileUploader";
const { uploadFileItem, fileInputRef, triggerFileInput, handleFileChange } = useFileUploader({ page: "chatinput", uploadApi: UploadDialogApi });
import { validators } from "@/utils/validators";
// 引入图标
import docxIcon from "@/assets/docx-icon.png";
import pdfIcon from "@/assets/pdf-icon.png";
import { projectStore } from "@/store/index";
const project = projectStore();

// ── 从知识库选择文件 ──────────────────────────────────────
// 知识库选择弹窗的显隐
const kbSelectVisible = ref(false);
// 记录哪些 docId 来自知识库（这些文件从对话框移除时不删服务器文件）
const kbDocIdSet = ref<Set<string>>(new Set());
// 打开知识库选择弹窗（需登录）
const openKbSelect = () => {
  if (!project.userInfo) {
    ElMessage("请登录再选择知识库文件");
    return;
  }
  // 最多3个文件限制
  if (uploadFileItem.value.length >= 3) {
    ElMessage("最多选择三个文件");
    return;
  }
  kbSelectVisible.value = true;
};
// 接收知识库选中的文件，加入对话框文件列表
const handleKbFileSelect = (file: KbFileListType[number]) => {
  // 防重复：同一个文件不能重复添加
  const exists = uploadFileItem.value.some((item) => item.docId === file.docId);
  if (exists) {
    ElMessage("该文件已添加");
    return;
  }
  // 数量限制
  if (uploadFileItem.value.length >= 3) {
    ElMessage("最多选择三个文件");
    return;
  }
  // 加入列表（知识库文件天然有 docId，可直接复用文档对话链路）
  uploadFileItem.value.push({
    fileName: file.fileName,
    fileType: file.fileType,
    fileSize: file.fileSize,
    docId: file.docId,
  });
  // 标记这个文件来自知识库
  kbDocIdSet.value.add(file.docId);
};

// ── 财报上传快速解读 ──────────────────────────────────────
const reportInputRef = ref<HTMLInputElement | null>(null);
// 触发财报文件选择
const triggerReportInput = () => {
  if (!project.userInfo) {
    ElMessage("请登录再上传财报");
    return;
  }
  reportInputRef.value?.click();
};
// 选择财报文件后：上传解读
const handleReportChange = async (env: Event) => {
  const input = env.target as HTMLInputElement;
  const files = input.files;
  if (!files || files.length <= 0) return;
  const file = files[0];

  // 校验大小（20MB）
  if (file.size > 20 * 1024 * 1024) {
    ElMessage("财报文件不能超过20MB");
    input.value = "";
    return;
  }

  project.disabledStatus = true;

  const formData = new FormData();
  formData.append("file", file);
  try {
    // 步骤1：上传财报，后端识别解读并存入 MongoDB（返回 docId）
    const res = await UploadReportApi(formData);
    input.value = "";

    if (!res.data.docId) {
      // 无法识别（如扫描件PDF），直接提示
      ElMessage(res.data.reportText || "财报识别失败");
      project.disabledStatus = false;
      return;
    }

    // 步骤2：把财报文件挂到对话框文件列表，然后自动发送消息走文档对话链路
    uploadFileItem.value = [
      {
        fileName: res.data.fileName || file.name,
        fileType: res.data.fileType || "IMG",
        fileSize: res.data.fileSize || (file.size / 1024).toFixed(2) + "kb",
        docId: res.data.docId,
      },
    ];
    userMessage.value = "请帮我解读这份财报的关键指标";
    project.disabledStatus = false;
    sendMessage();
  } catch (error) {
    input.value = "";
    ElMessage.error("财报识别失败");
    project.disabledStatus = false;
  }
};

// 删除文件
const deleteFile = async (docId: string, index: number) => {
  // 如果是来自知识库的文件，只从对话框列表移除，不删除服务器上的知识库文件
  if (kbDocIdSet.value.has(docId)) {
    uploadFileItem.value.splice(index, 1);
    kbDocIdSet.value.delete(docId);
    return;
  }
  // 否则是临时上传的文件，需要删除服务器文件
  // loading加载提示
  const loading = ElLoading.service({
    lock: true,
    text: "删除中...",
    background: "rgba(0, 0, 0, 0.8)",
  });
  try {
    await DeleteFileApi({ docId });
    loading.close();
    uploadFileItem.value.splice(index, 1);
  } catch (error) {
    loading.close();
  }
};
// 发送消息
const sendMessage = () => {
  // 校验
  validators.isNotEmpty(userMessage.value, "请输入内容");
  project.messageList.push(
    {
      role: "user",
      content: userMessage.value.trim(),
      ...(uploadFileItem.value.length > 0 && { uploadFileList: uploadFileItem.value }),
      ...(uploadFileItem.value.length > 0 && { displayContent: userMessage.value.trim() }),
    },
    {
      role: "assistant",
      content: "",
      loadingCircle: true,
    }
  );
  project.disabledStatus = true;
  project.chatWelcome = false;
  // 如果是新建对话，需要把问题加入对话列表的第一项里
  if (project.sessionId == "null") {
    console.log("进来");

    project.chatListData.unshift({ sessionId: "", content: userMessage.value.trim() });
    project.getSessIonIndex(0);
  }
  SendMessageApi({
    content: userMessage.value.trim(),
    sessionId: project.sessionId,
    uploadFileList: uploadFileItem.value,
    isKnowledgeBased: isKnowledgeBased.value,
  });
  // 清空输入框和临时文件
  userMessage.value = "";
  uploadFileItem.value = [];
  kbDocIdSet.value.clear();
};
// 终止模型输出
const stopOutput = () => {
  StopOutputApi({ sessionId: project.sessionId });
};
</script>

<style scoped>
/* 样式层 */
.chat-input {
  background-color: #f6f7fb;
  position: fixed;
  left: 230px;
  bottom: 0;
  right: 0;
  padding-bottom: 30px;
}
.chat-input-flex {
  display: flex;
  flex-direction: column;
  max-width: 968px;
  margin: 0 auto;
  background-color: #ffffff;
  border: 1px solid #615ced;
  padding: 15px;
  border-radius: 20px;
  box-shadow: 0 1px 11px 7px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}
.query-kb-button {
  width: fit-content;
  padding: initial;
  height: auto;
  border: 1px solid v-bind("queryKbStyle.border");
  border-radius: 20px;
  background-color: v-bind("queryKbStyle.backgroundColor");
  margin-bottom: 10px;
}
.query-kb {
  display: flex;
  align-items: center;
  padding: 7px;
}
.query-kb span {
  font-size: 14px;
  padding-left: 6px;
  color: v-bind("queryKbStyle.color");
}
.query-kb img {
  width: 15px;
  height: 15px;
}
.upload-file-list {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  padding-bottom: 15px;
}
.upload-file-item img {
  width: 30px;
  height: 30px;
  margin-right: 5px;
}
.upload-file-item {
  display: inline-flex;
  align-items: center;
  border: 1px solid #cecfdd;
  border-radius: 10px;
  max-width: 200px;
  padding: 5px;
  margin-right: 10px;
  position: relative;
}
.delete-file {
  position: absolute;
  bottom: 2px;
  right: 4px;
}
.upload-file-item span:nth-child(1) {
  font-size: 14px;
}
.upload-file-item span:nth-child(2) {
  font-size: 10px;
  color: #cecfdd;
}
.chat-input-content {
  display: flex;
  align-items: flex-end;
}
.upload-icon {
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  cursor: pointer;
}
.upload-icon img {
  width: 20px;
  height: 20px;
}
/* 上传下拉菜单 */
.upload-menu {
  display: flex;
  flex-direction: column;
}
.upload-menu-item {
  display: flex;
  align-items: center;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  color: #333;
  transition: background-color 0.2s;
}
.upload-menu-item:hover {
  background-color: #f3f2ff;
  color: #615ced;
}
.upload-menu-item .el-icon {
  margin-right: 8px;
  font-size: 16px;
}
/* 强制更改input样式 */
.chat-input-content >>> .el-textarea__inner:focus {
  box-shadow: none;
  border: none;
}
.chat-input-content >>> .el-textarea__inner {
  box-shadow: none;
  background: none;
  font-size: 16px;
}
.send-icon {
  width: 34px;
  height: auto;
}
.chat-input-content >>> .el-button {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  margin-left: 10px;
  background: none;
  border: none;
}
</style>
