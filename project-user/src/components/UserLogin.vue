<template>
  <!-- 登录界面 -->
  <el-dialog v-model="project.showLoginPopup" width="400" append-to-body :close-on-click-modal="false" :close-on-press-escape="false" align-center>
    <el-tabs v-model="activeName" class="demo-tabs">
      <el-tab-pane label="登录" name="Login">
        <el-input v-model="phoneNumber" placeholder="手机号" size="large"></el-input>
        <el-input v-model="passWord" placeholder="密码" size="large" type="password"></el-input>
        <el-button type="primary" size="default" @click="userLogin">登录</el-button>
      </el-tab-pane>
      <el-tab-pane label="注册" name="Register">
        <el-input v-model="phoneNumber" placeholder="手机号" size="large"></el-input>
        <el-input v-model="passWord" placeholder="密码" size="large" type="password"></el-input>
        <el-input v-model="confirmPassword" placeholder="确认密码" size="large" type="password"></el-input>
        <el-button type="primary" size="default" @click="userRegister">注册</el-button>
      </el-tab-pane>
    </el-tabs>
  </el-dialog>
</template>

<script setup lang="ts">
// 逻辑层
import { ref } from "vue";
import { validators } from "@/utils/validators";
import { UserRegisterApi, UserLoginApi, GetChatListApi } from "@/api/request";
import { projectStore } from "@/store/index";
import { getItemChat } from "@/api/getItemChat";
const project = projectStore();
const activeName = ref("Login");
const phoneNumber = ref("");
const passWord = ref("");
const confirmPassword = ref("");
// 注册
const userRegister = async () => {
  // 校验
  validators.isPhoneNumber(phoneNumber.value);
  validators.isPasswordValid(passWord.value);
  validators.isEqual(passWord.value, confirmPassword.value);
  await UserRegisterApi({
    phoneNumber: phoneNumber.value,
    password: passWord.value,
    confirmPassword: confirmPassword.value,
  });
  activeName.value = "Login";
};
// 登录
const userLogin = async () => {
  // 校验
  validators.isPhoneNumber(phoneNumber.value);
  validators.isPasswordValid(passWord.value);
  const res = await UserLoginApi({ phoneNumber: phoneNumber.value, password: passWord.value });
  console.log(res);
  project.showLoginPopup = false;
  // 缓存本地缓存
  project.userLogin(res.data);
  // 登录成功，立马获取对话列表数据
  const chatListData = await GetChatListApi();
  project.chatListData = chatListData.data;
  project.initUserFromStorage();
  if (project.sessionId !== "null") {
    await getItemChat(project.sessionIndex, project.sessionId);
  }
};
</script>

<style scoped>
/* 样式层 */
.demo-tabs >>> .el-tabs__item {
  font-size: 16px;
}
.demo-tabs >>> .el-input {
  margin-bottom: 20px;
}
.demo-tabs >>> .el-tab-pane {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}
</style>
