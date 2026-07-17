<template>
  <!-- 从知识库选择文件的弹窗 -->
  <el-dialog v-model="visible" title="从知识库选择文件" width="460" append-to-body @open="loadKbFiles">
    <!-- 文件列表（单选） -->
    <div class="kb-select-list" v-if="kbFileList.length > 0">
      <el-radio-group v-model="selectedDocId">
        <el-radio v-for="item in kbFileList" :key="item.docId" :value="item.docId" class="kb-select-item">
          <img :src="item.fileType == 'PDF' ? pdfIcon : docxIcon" alt="" />
          <span class="kb-file-name hidden-text">{{ item.fileName }}</span>
          <span class="kb-file-size">{{ item.fileSize }}</span>
        </el-radio>
      </el-radio-group>
    </div>
    <!-- 空状态 -->
    <div class="kb-empty" v-else>
      <span>知识库暂无文件，请先到「知识库管理」上传</span>
    </div>
    <!-- 底部操作按钮 -->
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :disabled="!selectedDocId" @click="confirmSelect">确认</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from "vue";
import type { KbFileListType } from "@/types/index";
import { KbFileListApi } from "@/api/request";
import docxIcon from "@/assets/docx-icon.png";
import pdfIcon from "@/assets/pdf-icon.png";

// 弹窗显隐（用 v-model 双向绑定父组件）
const visible = defineModel<boolean>({ default: false });
// 选中文件后通知父组件
const emit = defineEmits<{
  (e: "select", file: KbFileListType[number]): void;
}>();

// 知识库文件列表
const kbFileList = ref<KbFileListType>([]);
// 当前选中的文件 docId
const selectedDocId = ref("");

// 弹窗打开时加载知识库文件列表
const loadKbFiles = async () => {
  selectedDocId.value = "";
  const loading = ElLoading.service({
    lock: true,
    text: "加载中...",
    background: "rgba(0, 0, 0, 0.6)",
  });
  try {
    const res = await KbFileListApi();
    kbFileList.value = res.data;
    loading.close();
  } catch (error) {
    loading.close();
    ElMessage.error("加载知识库文件失败");
  }
};

// 点击确认：把选中的文件传回父组件
const confirmSelect = () => {
  const file = kbFileList.value.find((item) => item.docId === selectedDocId.value);
  if (!file) {
    ElMessage("请选择一个文件");
    return;
  }
  emit("select", file);
  visible.value = false;
};
</script>

<style scoped>
.kb-select-list {
  max-height: 360px;
  overflow-y: auto;
}
.kb-select-list >>> .el-radio-group {
  display: flex;
  flex-direction: column;
  width: 100%;
}
.kb-select-item {
  display: flex;
  align-items: center;
  width: 100%;
  height: auto;
  margin: 0 0 10px 0;
  padding: 8px;
  border: 1px solid #eceff3;
  border-radius: 10px;
}
.kb-select-item.is-checked {
  border-color: #615ced;
  background-color: #f3f2ff;
}
.kb-select-item >>> .el-radio__label {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
  padding-left: 8px;
}
.kb-select-item img {
  width: 28px;
  height: 28px;
  margin-right: 8px;
}
.kb-file-name {
  font-size: 14px;
  color: #333;
  flex: 1;
  min-width: 0;
}
.kb-file-size {
  font-size: 12px;
  color: #b0b0b0;
  margin-left: 8px;
}
.kb-empty {
  text-align: center;
  color: #999;
  padding: 40px 0;
  font-size: 14px;
}
</style>
