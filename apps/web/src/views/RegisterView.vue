<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { getErrorMessage } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const errorMessage = ref('')
const form = reactive({
  display_name: '',
  email: '',
  password: '',
})

async function submit(): Promise<void> {
  errorMessage.value = ''
  try {
    await authStore.register(form)
    ElMessage.success('注册成功')
    await router.push('/me')
  } catch (error) {
    errorMessage.value = getErrorMessage(error)
  }
}
</script>

<template>
  <div class="auth-page">
    <section class="auth-intro">
      <p class="section-kicker">建立个人书签</p>
      <h1>从一首熟悉的诗开始</h1>
      <p>注册后可以保留个人资料，后续还能维护收藏与问答历史。</p>
    </section>

    <form class="auth-form" @submit.prevent="submit">
      <header>
        <h2>注册</h2>
        <RouterLink to="/login">已有账号？登录</RouterLink>
      </header>

      <label>
        <span>显示名称</span>
        <input
          v-model.trim="form.display_name"
          type="text"
          autocomplete="nickname"
          maxlength="80"
          required
        />
      </label>
      <label>
        <span>邮箱</span>
        <input v-model.trim="form.email" type="email" autocomplete="email" required />
      </label>
      <label>
        <span>密码</span>
        <input
          v-model="form.password"
          type="password"
          autocomplete="new-password"
          minlength="8"
          required
        />
        <small>至少 8 个字符</small>
      </label>

      <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>
      <button class="wide-button" type="submit" :disabled="authStore.loading">
        {{ authStore.loading ? '正在创建账号' : '创建账号' }}
      </button>
    </form>
  </div>
</template>

