import { requestData, requestPage } from './http'
import type {
  Author,
  CategoryType,
  DomainLabel,
  DomainLabelAssignment,
  DomainLabelDimension,
  DomainLabelGenerationMethod,
  DomainLabelReviewAction,
  DomainLabelReviewStatus,
  DomainLabelStatus,
  Dynasty,
  Paginated,
  Poem,
  PoemCategory,
  PoemStatus,
} from '@/types/api'

export interface AdminPoemListParams {
  page?: number
  page_size?: number
  q?: string
  status?: PoemStatus
  include_deleted?: boolean
  author_id?: number
  dynasty_id?: number
  category_id?: number
}

export interface PoemPayload {
  title: string
  author_id: number | null
  dynasty_id: number | null
  content: string
  summary: string | null
  category_ids: number[]
  tag_names: string[]
}

export interface PoemUpdatePayload extends PoemPayload {
  version_no: number
}

export interface AuthorPayload {
  name: string
  aliases: string[]
  bio: string | null
  dynasty_id: number | null
}

export interface DynastyPayload {
  name: string
  description: string | null
  sort_order: number
}

export interface CategoryPayload {
  name: string
  type: CategoryType
  parent_id: number | null
  sort_order: number
  is_active: boolean
}

export interface DomainLabelPayload {
  dimension: DomainLabelDimension
  canonical_name: string
  description: string | null
  aliases: string[]
}

export interface DomainLabelUpdatePayload {
  canonical_name?: string
  description?: string | null
  status?: DomainLabelStatus
  merged_into_id?: number | null
  aliases?: string[]
}

export interface DomainLabelListParams {
  page?: number
  page_size?: number
  dimension?: DomainLabelDimension
  status?: DomainLabelStatus
  q?: string
}

export interface DomainLabelAssignmentListParams {
  page?: number
  page_size?: number
  review_status?: DomainLabelReviewStatus
  generation_method?: DomainLabelGenerationMethod
}

export const adminApi = {
  listPoems(params: AdminPoemListParams = {}): Promise<Paginated<Poem>> {
    return requestPage<Poem>({
      method: 'GET',
      url: '/admin/poems',
      params,
    })
  },

  getPoem(poemId: number): Promise<Poem> {
    return requestData<Poem>({
      method: 'GET',
      url: `/admin/poems/${poemId}`,
    })
  },

  createPoem(payload: PoemPayload): Promise<Poem> {
    return requestData<Poem>({
      method: 'POST',
      url: '/admin/poems',
      data: payload,
    })
  },

  updatePoem(poemId: number, payload: PoemUpdatePayload): Promise<Poem> {
    return requestData<Poem>({
      method: 'PATCH',
      url: `/admin/poems/${poemId}`,
      data: payload,
    })
  },

  deletePoem(poemId: number): Promise<null> {
    return requestData<null>({
      method: 'DELETE',
      url: `/admin/poems/${poemId}`,
    })
  },

  publishPoem(poemId: number): Promise<Poem> {
    return requestData<Poem>({
      method: 'POST',
      url: `/admin/poems/${poemId}/publish`,
    })
  },

  unpublishPoem(poemId: number): Promise<Poem> {
    return requestData<Poem>({
      method: 'POST',
      url: `/admin/poems/${poemId}/unpublish`,
    })
  },

  restorePoem(poemId: number): Promise<Poem> {
    return requestData<Poem>({
      method: 'POST',
      url: `/admin/poems/${poemId}/restore`,
    })
  },

  listAuthors(params: { page?: number; page_size?: number; q?: string; dynasty_id?: number } = {}) {
    return requestPage<Author>({
      method: 'GET',
      url: '/admin/authors',
      params,
    })
  },

  createAuthor(payload: AuthorPayload): Promise<Author> {
    return requestData<Author>({
      method: 'POST',
      url: '/admin/authors',
      data: payload,
    })
  },

  updateAuthor(authorId: number, payload: AuthorPayload): Promise<Author> {
    return requestData<Author>({
      method: 'PATCH',
      url: `/admin/authors/${authorId}`,
      data: payload,
    })
  },

  deleteAuthor(authorId: number): Promise<null> {
    return requestData<null>({
      method: 'DELETE',
      url: `/admin/authors/${authorId}`,
    })
  },

  listDynasties(): Promise<Dynasty[]> {
    return requestData<Dynasty[]>({
      method: 'GET',
      url: '/admin/dynasties',
    })
  },

  createDynasty(payload: DynastyPayload): Promise<Dynasty> {
    return requestData<Dynasty>({
      method: 'POST',
      url: '/admin/dynasties',
      data: payload,
    })
  },

  updateDynasty(dynastyId: number, payload: DynastyPayload): Promise<Dynasty> {
    return requestData<Dynasty>({
      method: 'PATCH',
      url: `/admin/dynasties/${dynastyId}`,
      data: payload,
    })
  },

  deleteDynasty(dynastyId: number): Promise<null> {
    return requestData<null>({
      method: 'DELETE',
      url: `/admin/dynasties/${dynastyId}`,
    })
  },

  listCategories(type?: CategoryType): Promise<PoemCategory[]> {
    return requestData<PoemCategory[]>({
      method: 'GET',
      url: '/admin/categories',
      params: type ? { type } : undefined,
    })
  },

  createCategory(payload: CategoryPayload): Promise<PoemCategory> {
    return requestData<PoemCategory>({
      method: 'POST',
      url: '/admin/categories',
      data: payload,
    })
  },

  updateCategory(categoryId: number, payload: Partial<CategoryPayload>): Promise<PoemCategory> {
    return requestData<PoemCategory>({
      method: 'PATCH',
      url: `/admin/categories/${categoryId}`,
      data: payload,
    })
  },

  disableCategory(categoryId: number): Promise<null> {
    return requestData<null>({
      method: 'DELETE',
      url: `/admin/categories/${categoryId}`,
    })
  },

  listDomainLabels(params: DomainLabelListParams = {}): Promise<Paginated<DomainLabel>> {
    return requestPage<DomainLabel>({
      method: 'GET',
      url: '/admin/domain-labels',
      params,
    })
  },

  createDomainLabel(payload: DomainLabelPayload): Promise<DomainLabel> {
    return requestData<DomainLabel>({
      method: 'POST',
      url: '/admin/domain-labels',
      data: payload,
    })
  },

  updateDomainLabel(
    labelId: number,
    payload: DomainLabelUpdatePayload,
  ): Promise<DomainLabel> {
    return requestData<DomainLabel>({
      method: 'PATCH',
      url: `/admin/domain-labels/${labelId}`,
      data: payload,
    })
  },

  listDomainLabelAssignments(
    labelId: number,
    params: DomainLabelAssignmentListParams = {},
  ): Promise<Paginated<DomainLabelAssignment>> {
    return requestPage<DomainLabelAssignment>({
      method: 'GET',
      url: `/admin/domain-labels/${labelId}/assignments`,
      params,
    })
  },

  reviewDomainLabelAssignment(
    assignmentId: number,
    action: DomainLabelReviewAction,
  ): Promise<DomainLabelAssignment> {
    return requestData<DomainLabelAssignment>({
      method: 'POST',
      url: `/admin/domain-label-assignments/${assignmentId}/review`,
      data: { action },
    })
  },
}
