import type { RouteRecordRaw } from 'vue-router'

import SiteLayout from '@/layouts/SiteLayout.vue'

export const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: SiteLayout,
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('@/views/HomeView.vue'),
        meta: { title: '诗词库' },
      },
      {
        path: 'poems',
        name: 'poem-list',
        component: () => import('@/views/PoemListView.vue'),
        meta: { title: '诗词' },
      },
      {
        path: 'poems/:poemId',
        name: 'poem-detail',
        component: () => import('@/views/PoemDetailView.vue'),
        meta: { title: '诗词详情' },
      },
      {
        path: 'authors/:authorId',
        name: 'author-detail',
        component: () => import('@/views/AuthorDetailView.vue'),
        meta: { title: '作者详情' },
      },
      {
        path: 'authors',
        name: 'author-list',
        component: () => import('@/views/AuthorListView.vue'),
        meta: { title: '作者' },
      },
      {
        path: 'search',
        name: 'search',
        component: () => import('@/views/SearchView.vue'),
        meta: { title: '搜索' },
      },
      {
        path: 'chat',
        name: 'chat-home',
        component: () => import('@/views/ChatView.vue'),
        meta: { title: '问诗', requiresAuth: true },
      },
      {
        path: 'chat/:conversationId',
        name: 'chat-detail',
        component: () => import('@/views/ChatView.vue'),
        meta: { title: '问诗', requiresAuth: true },
      },
      {
        path: 'admin/poems',
        name: 'admin-poems',
        component: () => import('@/views/AdminPoemView.vue'),
        meta: { title: '诗词管理', requiresAuth: true, roles: ['admin'] },
      },
      {
        path: 'admin/catalog',
        name: 'admin-catalog',
        component: () => import('@/views/AdminCatalogView.vue'),
        meta: { title: '目录管理', requiresAuth: true, roles: ['admin'] },
      },
      {
        path: 'admin',
        redirect: '/admin/poems',
      },
      {
        path: 'login',
        name: 'login',
        component: () => import('@/views/LoginView.vue'),
        meta: { title: '登录', guestOnly: true },
      },
      {
        path: 'register',
        name: 'register',
        component: () => import('@/views/RegisterView.vue'),
        meta: { title: '注册', guestOnly: true },
      },
      {
        path: 'me',
        name: 'profile',
        component: () => import('@/views/ProfileView.vue'),
        meta: { title: '个人中心', requiresAuth: true },
      },
      {
        path: '403',
        name: 'forbidden',
        component: () => import('@/views/ForbiddenView.vue'),
        meta: { title: '无权限' },
      },
      {
        path: ':pathMatch(.*)*',
        name: 'not-found',
        component: () => import('@/views/NotFoundView.vue'),
        meta: { title: '页面不存在' },
      },
    ],
  },
]
