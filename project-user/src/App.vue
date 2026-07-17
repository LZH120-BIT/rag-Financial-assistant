<script setup lang="ts"></script>

<template>
  <RouterView></RouterView>
</template>

<style>
body {
  margin: 0;
  padding: 0;
  background-color: #f6f7fb;
}
p,
div,
span {
  font-size: 16px;
  padding: 0;
  margin: 0;
  display: block;
}
/* 文字提示 0*/
.el-popper.is-customized {
  padding: 6px 12px;
  background: #876aff;
  color: #ffffff;
}

.el-popper.is-customized .el-popper__arrow::before {
  background: #876aff;
  right: 0;
}
/* 文本超过一行隐藏 */
.hidden-text {
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box !important;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 1;
  box-sizing: border-box;
}
/* 媒体查询 */
@media (max-width: 1460px) {
  .chat-message {
    max-width: 900px !important;
  }
  .chat-input-flex {
    max-width: calc(900px - 32px) !important;
  }
}
@media (max-width: 1330px) {
  .chat-message {
    max-width: 800px !important;
  }
  .chat-input-flex {
    max-width: calc(800px - 32px) !important;
  }
}
@media (max-width: 1160px) {
  .chat-message {
    max-width: 700px !important;
  }
  .chat-input-flex {
    max-width: calc(700px - 32px) !important;
  }
}
@media (max-width: 1060px) {
  .chat-message {
    width: 700px !important;
  }
  .chat-input-flex {
    width: calc(700px - 32px) !important;
  }
}
</style>
