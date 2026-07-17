// 获取某一个对话下的全部数据
import { SingleChatDataApi } from "@/api/request";
import { projectStore } from "@/store/index";
const project = projectStore();
export const getItemChat = async (index: number, sessionId: string) => {
  project.getSessIonIndex(index);
  project.getSessionId(sessionId);
  const loading = ElLoading.service({
    lock: true,
    text: "请求中...",
    background: "rgba(255,255,255,0.9)",
  });
  project.chatWelcome = false;
  const res = await SingleChatDataApi({ sessionId });
  console.log(res);
  project.messageList = res.data;
  loading.close();
};
