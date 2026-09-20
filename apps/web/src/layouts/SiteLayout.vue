<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const menuOpen = ref(false)

const navigation = computed(() => {
  const items = [
    { label: '诗词', to: '/poems' },
    { label: '作者', to: '/authors' },
    { label: '搜索', to: '/search' },
    { label: '问诗', to: '/chat' },
  ]
  if (authStore.isAdmin) {
    items.push({ label: '管理', to: '/admin/poems' })
  }
  return items
})

function closeMenu(): void {
  menuOpen.value = false
}

async function logout(): Promise<void> {
  await authStore.logout()
  closeMenu()
  await router.push('/')
}
</script>

<template>
  <div class="site-shell">
    <header class="site-header">
      <div class="site-header__inner">
        <RouterLink class="brand" to="/" aria-label="诗库首页" @click="closeMenu">
          <span class="brand__seal" aria-hidden="true">诗</span>
          <span>
            <strong>诗库</strong>
            <small>Poem RAG</small>
          </span>
        </RouterLink>

        <button
          class="menu-button"
          type="button"
          :aria-expanded="menuOpen"
          aria-controls="site-navigation"
          @click="menuOpen = !menuOpen"
        >
          目录
        </button>

        <nav id="site-navigation" class="site-nav" :class="{ 'site-nav--open': menuOpen }">
          <RouterLink
            v-for="item in navigation"
            :key="item.to"
            :to="item.to"
            @click="closeMenu"
          >
            {{ item.label }}
          </RouterLink>
        </nav>

        <div class="account-actions" :class="{ 'account-actions--open': menuOpen }">
          <template v-if="authStore.isAuthenticated">
            <RouterLink class="account-name" to="/me" @click="closeMenu">
              {{ authStore.user?.display_name }}
            </RouterLink>
            <button class="text-button" type="button" @click="logout">退出</button>
          </template>
          <template v-else>
            <RouterLink class="text-link" to="/login" @click="closeMenu">登录</RouterLink>
            <RouterLink class="primary-link" to="/register" @click="closeMenu">注册</RouterLink>
          </template>
        </div>
      </div>
    </header>

    <main class="site-main">
      <RouterView />
    </main>

    <footer class="site-footer">
      <div>
        <strong>诗库</strong>
        <span>收录、检索与理解中国古典诗词</span>
      </div>
      <span>v0.1 开发版</span>
    </footer>
  </div>
</template>
