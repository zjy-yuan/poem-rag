import type { Router } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

export function installRouteGuards(router: Router): void {
  router.beforeEach(async (to) => {
    const authStore = useAuthStore()
    if (!authStore.initialized) {
      await authStore.bootstrap()
    }

    document.title = to.meta.title ? `${to.meta.title} | 诗库` : '诗库'

    if (to.meta.guestOnly && authStore.isAuthenticated) {
      return { name: 'home' }
    }

    if (to.meta.requiresAuth && !authStore.isAuthenticated) {
      return {
        name: 'login',
        query: { redirect: to.fullPath },
      }
    }

    if (to.meta.roles?.length && !to.meta.roles.includes(authStore.user?.role ?? 'user')) {
      return { name: 'forbidden' }
    }

    return true
  })
}

