const routes  =[
  {
    path:"/",
    name:'appshell',
    component:()=>import("@/components/AppShell.vue")
  }
]
// 创建路由实例
import { createRouter,createWebHistory } from "vue-router";
const router = createRouter({
  // createWebHashHistory():带#号，===》适用于纯静态页面，seo效果差，基本上无需服务器干涉
  // createWebHistory():不带#号，====》seo效果号，需要服务器干涉，否则可能404
  history:createWebHistory(),
  routes
})

export default router