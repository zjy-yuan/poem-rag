import { requestData } from './http'
import type { CategoryType, Dynasty, PoemCategory } from '@/types/api'

export const catalogApi = {
  listCategories(type?: CategoryType): Promise<PoemCategory[]> {
    return requestData<PoemCategory[]>({
      method: 'GET',
      url: '/categories',
      params: type ? { type } : undefined,
    })
  },

  listDynasties(): Promise<Dynasty[]> {
    return requestData<Dynasty[]>({
      method: 'GET',
      url: '/dynasties',
    })
  },
}