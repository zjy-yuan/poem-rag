import { createRouter, createWebHistory } from 'vue-router'

import { installRouteGuards } from './guards'
import { routes } from './routes'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    requiresAuth?: boolean
    guestOnly?: boolean
    roles?: Array<'user' | 'admin'>
  }
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

installRouteGuards(router)

export default router

