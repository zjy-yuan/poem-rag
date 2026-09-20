import { requestData } from './http'
import type { AuthSession } from '@/types/api'

export interface RegisterPayload {
  email: string
  password: string
  display_name: string
}

export interface LoginPayload {
  email: string
  password: string
}

export const authApi = {
  register(payload: RegisterPayload) {
    return requestData<AuthSession>({
      method: 'POST',
      url: '/auth/register',
      data: payload,
    })
  },

  login(payload: LoginPayload) {
    return requestData<AuthSession>({
      method: 'POST',
      url: '/auth/login',
      data: payload,
    })
  },

  refresh() {
    return requestData<AuthSession>({
      method: 'POST',
      url: '/auth/refresh',
    })
  },

  logout() {
    return requestData<null>({
      method: 'POST',
      url: '/auth/logout',
    })
  },
}

