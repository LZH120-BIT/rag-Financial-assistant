const requestUrl = "http://127.0.0.1:7000";
import axios from "axios";
import type {
  UserRegisterType,
  UserLoginType,
  ApiResponse,
  UserInfoResType,
  KbFileListType,
  SendMessageType,
  AiMessageType,
  GetChatListType,
  MessageListType,
} from "@/types/index";
import { projectStore } from "@/store/index";
const project = projectStore();
// 创建一个实例
const axiosInstance = axios.create({
  baseURL: requestUrl,
});

// 拦截器：请求之前
axiosInstance.interceptors.request.use(
  (config) => {
    config.headers.Authorization = "Bearer " + project.userInfo?.token || "";
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// 拦截器：请求之后
axiosInstance.interceptors.response.use(
  (response) => {
    // 响应的正确正确结果
    return response.data;
  },
  (error: { status: number; response: { data: { data: any; message: string | string[] } } }) => {
    console.log(error);
    const status: number = error.status;
    const tips = typeof error.response.data.message === "string" ? error.response.data.message : error.response.data.message[0];
    switch (status) {
      case 404:
        console.error("接口不存在或者请求方式不对");
        break;
      case 422:
        ElMessage(tips);
        break;
      case 400:
        ElMessage(tips);
        break;
      case 401:
        project.showLoginPopup = true;
        localStorage.removeItem("sessionId");
        localStorage.removeItem("sessionIndex");
        project.messageList = [];
        console.error("没有操作权限");
        break;
      case 500:
        ElMessage("服务器发生错误");
        break;
      case 501:
        ElMessage("服务器发生错误");
        break;
      case 502:
        ElMessage("服务器发生错误");
        break;
    }
    return Promise.reject(error);
  }
);

// 用户请求和模型对话，模型流式输出
export const SendMessageApi = async (params: SendMessageType) => {
  const { content, sessionId, uploadFileList, isKnowledgeBased } = params;
  const body: Record<string, any> = {
    content,
    sessionId,
    isKnowledgeBased,
  };
  if (uploadFileList && uploadFileList.length > 0) {
    body.uploadFileList = uploadFileList;
  }
  const response = await fetch(`${requestUrl}/chat/sendmessage`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + project.userInfo?.token || "",
    },
    body: JSON.stringify(body),
  });
  const aiMessageObj = project.messageList[project.messageList.length - 1];
  if (!response.ok) {
    const errorText = await response.text();
    ElMessage("发送失败");
    // 如果没有登录
    const errorInfo = JSON.parse(errorText);
    if (errorInfo.statusCode && errorInfo.statusCode == 401) {
      project.showLoginPopup = true;
      localStorage.removeItem("sessionId");
      localStorage.removeItem("sessionIndex");
      project.messageList = [];
    }
    aiMessageObj.content = "发送失败";
    aiMessageObj.loadingCircle = false;
    project.disabledStatus = false;
    throw new Error("发送失败" + errorText);
  }
  const reader = response.body?.getReader();
  if (!reader) {
    ElMessage("发送失败,你可以重试");
    aiMessageObj.loadingCircle = false;
    project.disabledStatus = false;
    throw new Error("模型响应失败");
  }
  console.log("开始流式输出数据");
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      console.log("大模型回复完毕");
      aiMessageObj.loadingCircle = false;
      project.disabledStatus = false;
      break;
    }
    // --------------处理模型回复的数据------------
    // 用utf-8的解码器把二进制转换成字符串
    const chunk = new TextDecoder("utf-8").decode(value, { stream: true });
    let parts = chunk.split("###ABC###");
    for (const part of parts) {
      if (part.trim() === "") continue;
      const aiMessage = JSON.parse(part) as AiMessageType;
      console.log(aiMessage);
      // 取会话id
      if (aiMessage.role === "sessionId") {
        project.getSessionId(aiMessage.content);
      }
      // 取文档或知识库的提示
      if (aiMessage.type) {
        aiMessageObj.readFileData = aiMessage;
      }
      // 取模型回复的数据
      if (aiMessage.role === "assistant") {
        aiMessageObj.loadingCircle = false;
        if (aiMessage.content && aiMessage.content.trim() !== "") {
          aiMessageObj.content += aiMessage.content;
        }
      }
    }
  }
};

// 注册接口
export const UserRegisterApi = (params: UserRegisterType): Promise<ApiResponse<[]>> => {
  return axiosInstance.post("/userinfo/registeruser", params);
};
// 登录接口
export const UserLoginApi = (params: UserLoginType): Promise<ApiResponse<UserInfoResType>> => {
  return axiosInstance.post("/userinfo/loginuser", params);
};
// 对话框上传文件
export const UploadDialogApi = (params: FormData): Promise<ApiResponse<string[]>> => {
  return axiosInstance.post("/fileanagement/uploaddialog", params);
};
// 对话框删除指定文件
export const DeleteFileApi = (params: { docId: string }): Promise<ApiResponse<[]>> => {
  return axiosInstance.post("/fileanagement/deletefile", params);
};
// 知识库文件上传
export const UploadkbApi = (params: FormData): Promise<ApiResponse<string[]>> => {
  return axiosInstance.post("/fileanagement/uploadkb", params);
};
// 知识库删除指定文件
export const DeleteFileKbApi = (params: { docId: string }): Promise<ApiResponse<[]>> => {
  return axiosInstance.post("/fileanagement/deletefilekb", params);
};
// 获取知识库文件列表
export const KbFileListApi = (): Promise<ApiResponse<KbFileListType>> => {
  return axiosInstance.get("/fileanagement/kbfilelist");
};
// 解析网页并加入知识库
export const UploadWebApi = (params: { url: string }): Promise<ApiResponse<{ docId: string; fileName: string }>> => {
  return axiosInstance.post("/fileanagement/uploadweb", params);
};
// 财报上传 + 快速解读
export const UploadReportApi = (
  params: FormData
): Promise<
  ApiResponse<{
    reportText: string;
    docId: string | null;
    sessionId?: string;
    fileName?: string;
    fileType?: string;
    fileSize?: string;
  }>
> => {
  return axiosInstance.post("/fileanagement/uploadreport", params);
};
// 获取对话列表数据
export const GetChatListApi = (): Promise<ApiResponse<GetChatListType[]>> => {
  return axiosInstance.get("/chat/getchatlist");
};
// 获取某个会话的对话数据
export const SingleChatDataApi = (params: { sessionId: string }): Promise<ApiResponse<MessageListType[]>> => {
  return axiosInstance.get("/chat/singlechatdata", { params });
};
// 终止模型输出
export const StopOutputApi = (params: { sessionId: string }): Promise<ApiResponse<[]>> => {
  return axiosInstance.get("/chat/stopoutput", { params });
};
// 删除某个会话
export const DeleteChatApi = (params: { sessionId: string }): Promise<ApiResponse<[]>> => {
  return axiosInstance.post("/chat/deletechat", params);
};
