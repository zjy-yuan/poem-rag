import { requestData, requestPage } from './http'
import type { Author, AuthorDetail, Paginated } from '@/types/api'

export interface AuthorListParams {
  page?: number
  page_size?: number
  q?: string
  dynasty_id?: number
}

export const authorsApi = {
  list(params: AuthorListParams = {}): Promise<Paginated<Author>> {
    return requestPage<Author>({
      method: 'GET',
      url: '/authors',
      params,
    })
  },

  get(authorId: number): Promise<AuthorDetail> {
    return requestData<AuthorDetail>({
      method: 'GET',
      url: `/authors/${authorId}`,
    })
  },
}