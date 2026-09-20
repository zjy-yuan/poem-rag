import { requestData, requestPage } from './http'
import type { Paginated, Poem } from '@/types/api'

export interface PoemListParams {
  page?: number
  page_size?: number
  q?: string
  author_id?: number
  dynasty_id?: number
  category_id?: number
}

export interface PoemSearchParams extends PoemListParams {
  q: string
}

export const poemsApi = {
  list(params: PoemListParams = {}): Promise<Paginated<Poem>> {
    return requestPage<Poem>({
      method: 'GET',
      url: '/poems',
      params,
    })
  },

  get(poemId: number): Promise<Poem> {
    return requestData<Poem>({
      method: 'GET',
      url: `/poems/${poemId}`,
    })
  },

  search(params: PoemSearchParams): Promise<Paginated<Poem>> {
    return requestPage<Poem>({
      method: 'GET',
      url: '/search',
      params,
    })
  },
}