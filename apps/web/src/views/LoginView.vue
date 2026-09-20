<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { getErrorMessage } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const errorMessage = ref('')
const form = reactive({
  email: '',
  password: '',
})

async function submit(): Promise<void> {
  errorMessage.value = ''
  try {
    await authStore.login(form)
    ElMessage.success('登录成功')
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.push(redirect)
  } catch (error) {
    errorMessage.value = getErrorMessage(error)
  }
}
</script>

<template>
  <div class="auth-page">
    <section class="auth-intro">
      <p class="section-kicker">欢迎回来</p>
      <h1>继续你的诗词旅程</h1>
      <p>登录后可以保存问答记录、个人资料和后续收藏内容。</p>
    </section>

    <form class="auth-form" @submit.prevent="submit">
      <header>
        <h2>登录</h2>
        <RouterLink to="/register">还没有账号？注册</RouterLink>
      </header>

      <label>
        <span>邮箱</span>
        <input v-model.trim="form.email" type="email" autocomplete="email" required />
      </label>
      <label>
        <span>密码</span>
        <input
          v-model="form.password"
          type="password"
          autocomplete="current-password"
          required
        />
      </label>

      <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>
      <button class="wide-button" type="submit" :disabled="authStore.loading">
        {{ authStore.loading ? '正在登录' : '登录' }}
      </button>
    </form>
  </div>
</template>

