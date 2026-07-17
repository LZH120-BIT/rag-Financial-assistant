<template>
  <!-- 欢迎页面 -->
  <div class="chat-window">
    <div class="chat-message">
      <img src="../assets/zhanshi.png" alt="" class="display-image" />
      <span class="greeting-title">我是你的财报问答助手 · Your Financial Report Assistant</span>
      <span class="greeting-span">上传上市公司年报或财务报表（支持中英文 · CN & EN），我可以帮你快速解读关键指标、对比同行数据、梳理经营要点，让你看懂数字背后的故事。</span>
    </div>
  </div>
</template>

<script>
// 逻辑层
</script>

<style scoped>
/* 样式层 */
.chat-window {
  width: 100%;
  margin-left: 230px;
  display: flex;
}
.chat-message {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  max-width: 1000px;
  margin: 0 auto;
  padding-bottom: 90px;
}
.display-image {
  width: 280px;
}
.greeting-title {
  font-size: 25px;
  padding-top: 15px;
}
.greeting-span {
  padding-top: 15px;
}
</style>
