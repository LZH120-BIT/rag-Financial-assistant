<template>
  <!-- 知识库弹窗 -->
  <el-drawer v-model="project.knowledgePopup" title="个人知识库管理" append-to-body size="600">
    <div class="file-data-list">
      <div v-for="(item, index) in uploadFileItem" :key="index">
        <img :src="item.fileType == 'PDF' ? pdfIcon : docxIcon" alt="" />
        <span class="hidden-text">{{ item.fileName }}</span>
        <el-icon class="delete-file" @click="deleteFileKb(item.docId, index)"><CloseBold /></el-icon>
      </div>
    </div>
    <div style="height: 70px"></div>
    <div class="file-upload">
      <input type="file" multiple :accept="uploadFileType" style="display: none" ref="fileInputRef" @change="handleFileChange" />
      <el-tooltip content="每次最多上传3个文件(每个5MB),仅支持PDF,DOCX文件类型" effect="customized" placement="top">
        <el-button type="primary" @click="triggerFileInput">上传文件</el-button>
      </el-tooltip>
      <el-button type="success" @click="webDialogVisible = true">添加网页</el-button>
    </div>
  </el-drawer>

  <!-- 添加网页弹窗 -->
  <el-dialog v-model="webDialogVisible" title="添加网页到知识库" width="480" append-to-body>
    <div class="web-dialog-tip">输入网页链接，系统会自动抓取正文并加入知识库（适合文章、百科类页面）。</div>
    <el-input v-model="webUrl" placeholder="请输入网页链接，如 https://..." clearable />
    <template #footer>
      <el-button @click="webDialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="webLoading" @click="addWebPage">添加</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
// 逻辑层
import { projectStore } from "@/store/index";
const project = projectStore();
import { CloseBold } from "@element-plus/icons-vue";
// 文件上传的类型
const uploadFileType = "application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
// 文件上传
import { UploadkbApi, DeleteFileKbApi, KbFileListApi, UploadWebApi } from "@/api/request";
import { useFileUploader } from "@/api/useFileUploader";
const { uploadFileItem, fileInputRef, triggerFileInput, handleFileChange } = useFileUploader({ page: "chatinput", uploadApi: UploadkbApi });
// 引入图标
import docxIcon from "@/assets/docx-icon.png";
import pdfIcon from "@/assets/pdf-icon.png";
import { ref, watch } from "vue";

// ── 添加网页到知识库 ──────────────────────────────────
const webDialogVisible = ref(false);
const webUrl = ref("");
const webLoading = ref(false);
const addWebPage = async () => {
  const url = webUrl.value.trim();
  if (!url) {
    ElMessage("请输入网页链接");
    return;
  }
  // 简单校验是否是 http(s) 链接
  if (!/^https?:\/\/.+/.test(url)) {
    ElMessage("请输入有效的网页链接（以 http:// 或 https:// 开头）");
    return;
  }
  webLoading.value = true;
  try {
    const res = await UploadWebApi({ url });
    // 把新网页加入列表显示
    uploadFileItem.value.push({
      docId: res.data.docId,
      fileName: res.data.fileName,
      fileType: "WEB" as any,
      fileSize: "",
    });
    ElMessage.success("网页已加入知识库");
    webUrl.value = "";
    webDialogVisible.value = false;
  } catch (error) {
    // 错误提示由全局拦截器处理
  } finally {
    webLoading.value = false;
  }
};
// 删除知识库文件
const deleteFileKb = async (docId: string, index: number) => {
  // loading加载提示
  const loading = ElLoading.service({
    lock: true,
    text: "删除中...",
    background: "rgba(0, 0, 0, 0.8)",
  });
  try {
    await DeleteFileKbApi({ docId });
    loading.close();
    uploadFileItem.value.splice(index, 1);
  } catch (error) {
    loading.close();
  }
};
// 请求知识库文件
let count = 0;
watch(
  () => project.knowledgePopup,
  async (newVal) => {
    console.log(newVal);
    if (newVal) {
      count += 1;
      if (count === 1) {
        const res = await KbFileListApi();
        console.log(res);
        uploadFileItem.value = res.data;
      }
    }
  }
);
</script>

<style scoped>
/* 样式层 */
.web-dialog-tip {
  font-size: 13px;
  color: #909399;
  margin-bottom: 12px;
  line-height: 1.5;
}
.file-data-list {
  /* 网格布局 */
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: 20px;
}
.file-data-list img {
  width: 70px;
  height: 70px;
}
.file-data-list div {
  border: 1px solid #888;
  padding: 7px;
  border-radius: 10px;
  position: relative;
}
.delete-file {
  position: absolute;
  right: 4px;
  top: 4px;
  opacity: 0;
  cursor: pointer;
  transition: opacity 0.2s;
}
.file-data-list div:hover .delete-file {
  opacity: 1;
}
.file-upload {
  background-color: #ffffff;
  position: fixed;
  bottom: 0;
  right: 0;
  width: 600px;
  height: 70px;
  display: flex;
  justify-content: center;
  align-items: center;
}
</style>
