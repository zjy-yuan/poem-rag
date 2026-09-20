import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { authApi, type LoginPayload, type RegisterPayload } from '@/api/auth'
import {
  getAccessToken,
  setAccessToken,
  setRefreshHandler,
} from '@/api/session'
import { usersApi } from '@/api/users'
import type { AuthSession, User } from '@/types/api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const initialized = ref(false)
  const loading = ref(false)

  const isAuthenticated = computed(() => user.value !== null && getAccessToken() !== null)
  const isAdmin = computed(() => user.value?.role === 'admin')

  function applySession(session: AuthSession): void {
    setAccessToken(session.access_token)
    user.value = session.user
  }

  function clearSession(): void {
    setAccessToken(null)
    user.value = null
  }

  async function refreshSession(): Promise<AuthSession> {
    const session = await authApi.refresh()
    applySession(session)
    return session
  }

  setRefreshHandler(async () => {
    try {
      await refreshSession()
      return getAccessToken()
    } catch {
      clearSession()
      return null
    }
  })

  async function bootstrap(): Promise<void> {
    if (initialized.value) {
      return
    }
    loading.value = true
    try {
      await refreshSession()
    } catch {
      clearSession()
    } finally {
      loading.value = false
      initialized.value = true
    }
  }

  async function login(payload: LoginPayload): Promise<void> {
    loading.value = true
    try {
      applySession(await authApi.login(payload))
      initialized.value = true
    } finally {
      loading.value = false
    }
  }

  async function register(payload: RegisterPayload): Promise<void> {
    loading.value = true
    try {
      applySession(await authApi.register(payload))
      initialized.value = true
    } finally {
      loading.value = false
    }
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } finally {
      clearSession()
    }
  }

  async function updateDisplayName(displayName: string): Promise<void> {
    user.value = await usersApi.updateMe(displayName)
  }

  async function changePassword(
    currentPassword: string,
    newPassword: string,
  ): Promise<void> {
    await usersApi.changePassword(currentPassword, newPassword)
  }

  return {
    user,
    initialized,
    loading,
    isAuthenticated,
    isAdmin,
    bootstrap,
    login,
    register,
    logout,
    updateDisplayName,
    changePassword,
  }
})

