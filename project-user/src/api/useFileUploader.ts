// -------------------------文件上传：对话框，知识库
import { ref } from "vue";
import type { KbFileListType } from "@/types/index";
import { projectStore } from "@/store/index";
// import { ElLoading } from "element-plus";
const pinia = projectStore();

interface UseFileUploaderOptions {
  page: "knowledge" | "chatinput";
  uploadApi: (formData: FormData) => Promise<{ data: string[] }>;
}
export function useFileUploader(options: UseFileUploaderOptions) {
  // 临时存储上传的文件
  const uploadFileItem = ref<KbFileListType>([]);
  // 调用input上传
  const fileInputRef = ref<HTMLInputElement | null>(null);
  const triggerFileInput = () => {
    fileInputRef.value?.click();
  };
  // 本地上传文件触发
  const handleFileChange = async (env: Event) => {
    const input = env.target as HTMLInputElement;
    const files = input.files as FileList;
    console.log(files);
    if (files.length <= 0) return false;
    // 如果没有登陆，禁止上传
    if (!pinia.userInfo) {
      ElMessage("请登录再上传");
      return false;
    }
    // 每次最多选择三个文件
    if (files.length > 3) {
      input.value = "";
      ElMessage("每次最多选择三个文件");
      return false;
    }
    // 如果是对话框上传文件，最多只能上传三个
    if (options.page === "chatinput") {
      if (uploadFileItem.value.length >= 3) {
        ElMessage("最多上传三个文件");
        return false;
      }
    }
    // loading加载提示
    const loading = ElLoading.service({
      lock: true,
      text: "解析中...",
      background: "rgba(0, 0, 0, 0.8)",
    });
    const formData = new FormData();
    // 过滤掉文件不是pdf和docx，并且大于5mb的
    const allowedTypes = ["application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/pdf"];
    const maxSize = 5 * 1024 * 1024;
    for (const file of files) {
      if (allowedTypes.includes(file.type) && file.size <= maxSize) {
        formData.append("file", file);
      }
    }
    // 记录一个现有数量
    const existingCount = uploadFileItem.value.length;
    // 视图层展示
    const formDataFiles = formData.getAll("file") as File[];
    console.log(formDataFiles);
    formDataFiles.forEach((file) => {
      uploadFileItem.value.push({
        fileName: file.name,
        fileType: file.type == "application/pdf" ? "PDF" : "DOCX",
        fileSize: (file.size / 1024).toFixed(2) + "kb",
        docId: "",
      });
    });
    // 上传服务器
    try {
      const res = await options.uploadApi(formData);
      console.log(res);
      res.data.forEach((item, index) => {
        const targetIndex = existingCount + index;
        uploadFileItem.value[targetIndex].docId = item;
      });
      loading.close();
    } catch (error) {
      loading.close();
      ElMessage("上传出错");
      uploadFileItem.value = uploadFileItem.value.filter((item) => item.docId !== "");
    }
  };
  return {
    uploadFileItem,
    fileInputRef,
    triggerFileInput,
    handleFileChange,
  };
}
