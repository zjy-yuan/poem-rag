<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { getErrorMessage } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const profileError = ref('')
const passwordError = ref('')
const profile = reactive({
  displayName: authStore.user?.display_name ?? '',
})
const password = reactive({
  current: '',
  next: '',
})

async function saveProfile(): Promise<void> {
  profileError.value = ''
  try {
    await authStore.updateDisplayName(profile.displayName)
    ElMessage.success('资料已更新')
  } catch (error) {
    profileError.value = getErrorMessage(error)
  }
}

async function savePassword(): Promise<void> {
  passwordError.value = ''
  try {
    await authStore.changePassword(password.current, password.next)
    password.current = ''
    password.next = ''
    ElMessage.success('密码已更新')
  } catch (error) {
    passwordError.value = getErrorMessage(error)
  }
}
</script>

<template>
  <div class="page profile-page">
    <header class="page-heading">
      <div>
        <p class="section-kicker">个人中心</p>
        <h1>{{ authStore.user?.display_name }}</h1>
      </div>
      <p>{{ authStore.user?.email }}</p>
    </header>

    <div class="settings-grid">
      <form class="settings-panel" @submit.prevent="saveProfile">
        <header>
          <h2>基本资料</h2>
          <p>修改后在页面导航中显示的名称。</p>
        </header>
        <label>
          <span>显示名称</span>
          <input v-model.trim="profile.displayName" type="text" maxlength="80" required />
        </label>
        <p v-if="profileError" class="form-error" role="alert">{{ profileError }}</p>
        <button type="submit">保存资料</button>
      </form>

      <form class="settings-panel" @submit.prevent="savePassword">
        <header>
          <h2>修改密码</h2>
          <p>新密码至少 8 个字符。</p>
        </header>
        <label>
          <span>当前密码</span>
          <input v-model="password.current" type="password" autocomplete="current-password" />
        </label>
        <label>
          <span>新密码</span>
          <input
            v-model="password.next"
            type="password"
            autocomplete="new-password"
            minlength="8"
          />
        </label>
        <p v-if="passwordError" class="form-error" role="alert">{{ passwordError }}</p>
        <button type="submit">更新密码</button>
      </form>
    </div>
  </div>
</template>

