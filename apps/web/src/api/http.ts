import axios, {
  AxiosError,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios'

import { getAccessToken, refreshAccessToken } from './session'
import type {
  ApiError,
  ApiResponse,
  Paginated,
  PaginationMeta,
} from '@/types/api'

interface RetryableRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean
}

export class ApiClientError extends Error {
  readonly code: string
  readonly details: ApiError['details']
  readonly status?: number

  constructor(error: ApiError, status?: number) {
    super(error.message)
    this.name = 'ApiClientError'
    this.code = error.code
    this.details = error.details
    this.status = status
  }
}

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api/v1',
  withCredentials: true,
  timeout: 15_000,
  headers: {
    'Content-Type': 'application/json',
  },
})

http.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiResponse<unknown>>) => {
    const original = error.config as RetryableRequestConfig | undefined
    const status = error.response?.status

    if (
      status === 401 &&
      original &&
      !original._retry &&
      !original.url?.includes('/auth/refresh') &&
      !original.url?.includes('/auth/login')
    ) {
      original._retry = true
      const token = await refreshAccessToken()
      if (token) {
        original.headers.Authorization = `Bearer ${token}`
        return http(original)
      }
    }

    const payload = error.response?.data
    if (payload?.error) {
      return Promise.reject(new ApiClientError(payload.error, status))
    }
    return Promise.reject(
      new ApiClientError(
        {
          code: 'NETWORK_ERROR',
          message: '无法连接到服务，请检查后端是否已启动',
          details: [],
        },
        status,
      ),
    )
  },
)

function ensureSuccess<T>(response: ApiResponse<T>, status: number): T {
  if (!response.success) {
    throw new ApiClientError(
      response.error ?? {
        code: 'INVALID_RESPONSE',
        message: '服务返回了无法识别的响应',
        details: [],
      },
      status,
    )
  }
  return response.data as T
}

function normalizeMeta(meta: ApiResponse<unknown>['meta']): PaginationMeta {
  return {
    page: meta?.page ?? 1,
    page_size: meta?.page_size ?? 20,
    total: meta?.total ?? 0,
    total_pages: meta?.total_pages ?? 0,
  }
}

export async function requestData<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await http.request<ApiResponse<T>>(config)
  return ensureSuccess(response.data, response.status)
}

export async function requestNoContent(config: AxiosRequestConfig): Promise<void> {
  const response = await http.request<ApiResponse<null>>(config)
  if (response.status !== 204) {
    ensureSuccess(response.data, response.status)
  }
}

export async function requestPage<T>(config: AxiosRequestConfig): Promise<Paginated<T>> {
  const response = await http.request<ApiResponse<T[]>>(config)
  return {
    items: ensureSuccess(response.data, response.status) ?? [],
    meta: normalizeMeta(response.data.meta),
  }
}

export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    return error.message
  }
  if (error instanceof Error) {
    return error.message
  }
  return '操作失败，请稍后重试'
}
