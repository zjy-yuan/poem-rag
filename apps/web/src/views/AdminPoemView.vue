<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import AdminNav from '@/components/AdminNav.vue'
import { adminApi, type PoemPayload } from '@/api/admin'
import { getErrorMessage } from '@/api/http'
import type {
  Author,
  Dynasty,
  PaginationMeta,
  Poem,
  PoemCategory,
  PoemStatus,
} from '@/types/api'

const statusOptions: Array<{ value: PoemStatus; label: string }> = [
  { value: 'draft', label: '草稿' },
  { value: 'published', label: '已发布' },
  { value: 'archived', label: '已归档' },
]

interface PoemForm {
  title: string
  authorId: number | ''
  dynastyId: number | ''
  content: string
  summary: string
  categoryIds: number[]
  tagText: string
}

const filters = reactive({
  q: '',
  status: '' as PoemStatus | '',
  authorId: '' as number | '',
  dynastyId: '' as number | '',
  includeDeleted: false,
})

const form = reactive<PoemForm>({
  title: '',
  authorId: '',
  dynastyId: '',
  content: '',
  summary: '',
  categoryIds: [],
  tagText: '',
})

const poems = ref<Poem[]>([])
const authors = ref<Author[]>([])
const dynasties = ref<Dynasty[]>([])
const categories = ref<PoemCategory[]>([])
const meta = ref<PaginationMeta>({ page: 1, page_size: 20, total: 0, total_pages: 0 })
const currentPage = ref(1)
const isLoading = ref(true)
const optionsLoading = ref(true)
const optionsError = ref('')
const errorMessage = ref('')
const editorOpen = ref(false)
const editorLoading = ref(false)
const editorSaving = ref(false)
const editorError = ref('')
const editingPoemId = ref<number | null>(null)
const editingVersion = ref(0)
const actionPoemId = ref<number | null>(null)
let requestGeneration = 0

const editorTitle = computed(() => (editingPoemId.value === null ? '新建诗词' : '编辑诗词'))
const editorSubmitLabel = computed(() =>
  editingPoemId.value === null ? '创建草稿' : '保存修改',
)
const activeCategories = computed(() => categories.value.filter((item) => item.is_active))
const parentCategories = computed(() => activeCategories.value)

function statusLabel(status: PoemStatus): string {
  return statusOptions.find((item) => item.value === status)?.label ?? status
}

function isDeleted(poem: Poem): boolean {
  return poem.deleted_at !== null
}

function rowStatusLabel(poem: Poem): string {
  return isDeleted(poem) ? '已删除' : statusLabel(poem.status)
}

function statusClass(poem: Poem): string {
  if (isDeleted(poem)) {
    return 'admin-status admin-status--deleted'
  }
  return `admin-status admin-status--${poem.status}`
}

function formatDate(value: string | null): string {
  if (!value) {
    return '未发布'
  }
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

async function loadOptions(): Promise<void> {
  optionsLoading.value = true
  optionsError.value = ''
  try {
    const [authorPage, dynastyItems, categoryItems] = await Promise.all([
      adminApi.listAuthors({ page_size: 100 }),
      adminApi.listDynasties(),
      adminApi.listCategories(),
    ])
    authors.value = authorPage.items
    dynasties.value = dynastyItems
    categories.value = categoryItems
  } catch (error) {
    optionsError.value = getErrorMessage(error)
  } finally {
    optionsLoading.value = false
  }
}

async function loadPoems(): Promise<void> {
  const generation = ++requestGeneration
  isLoading.value = true
  errorMessage.value = ''
  try {
    const result = await adminApi.listPoems({
      page: currentPage.value,
      page_size: meta.value.page_size,
      q: filters.q.trim() || undefined,
      status: filters.status || undefined,
      include_deleted: filters.includeDeleted || undefined,
      author_id: filters.authorId === '' ? undefined : filters.authorId,
      dynasty_id: filters.dynastyId === '' ? undefined : filters.dynastyId,
    })
    if (generation !== requestGeneration) {
      return
    }
    poems.value = result.items
    meta.value = result.meta
  } catch (error) {
    if (generation !== requestGeneration) {
      return
    }
    poems.value = []
    errorMessage.value = getErrorMessage(error)
  } finally {
    if (generation === requestGeneration) {
      isLoading.value = false
    }
  }
}

async function applyFilters(): Promise<void> {
  currentPage.value = 1
  await loadPoems()
}

async function clearFilters(): Promise<void> {
  filters.q = ''
  filters.status = ''
  filters.authorId = ''
  filters.dynastyId = ''
  filters.includeDeleted = false
  currentPage.value = 1
  await loadPoems()
}

async function goToPage(page: number): Promise<void> {
  if (page < 1 || page > meta.value.total_pages || page === currentPage.value) {
    return
  }
  currentPage.value = page
  await loadPoems()
}

function resetForm(): void {
  form.title = ''
  form.authorId = ''
  form.dynastyId = ''
  form.content = ''
  form.summary = ''
  form.categoryIds = []
  form.tagText = ''
  editingPoemId.value = null
  editingVersion.value = 0
  editorError.value = ''
}

function assignForm(poem: Poem): void {
  form.title = poem.title
  form.authorId = poem.author_id ?? ''
  form.dynastyId = poem.dynasty_id ?? ''
  form.content = poem.content
  form.summary = poem.summary ?? ''
  form.categoryIds = poem.categories.map((item) => item.id)
  form.tagText = poem.tags.map((item) => item.name).join(', ')
  editingPoemId.value = poem.id
  editingVersion.value = poem.version_no
  editorError.value = ''
}

function openCreate(): void {
  resetForm()
  editorOpen.value = true
}

async function openEdit(poem: Poem): Promise<void> {
  resetForm()
  editorOpen.value = true
  editorLoading.value = true
  try {
    assignForm(await adminApi.getPoem(poem.id))
  } catch (error) {
    editorError.value = getErrorMessage(error)
  } finally {
    editorLoading.value = false
  }
}

function closeEditor(): void {
  if (editorSaving.value) {
    return
  }
  editorOpen.value = false
  resetForm()
}

function parseTagNames(): string[] {
  const seen = new Set<string>()
  return form.tagText
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter((item) => {
      if (!item || seen.has(item)) {
        return false
      }
      seen.add(item)
      return true
    })
}

async function savePoem(): Promise<void> {
  editorError.value = ''
  if (!form.title.trim() || !form.content.trim()) {
    editorError.value = '标题和正文不能为空。'
    return
  }

  const payload: PoemPayload = {
    title: form.title.trim(),
    author_id: form.authorId === '' ? null : form.authorId,
    dynasty_id: form.dynastyId === '' ? null : form.dynastyId,
    content: form.content.trim(),
    summary: form.summary.trim() || null,
    category_ids: [...form.categoryIds],
    tag_names: parseTagNames(),
  }

  editorSaving.value = true
  try {
    if (editingPoemId.value === null) {
      await adminApi.createPoem(payload)
      ElMessage.success('诗词已创建为草稿')
    } else {
      await adminApi.updatePoem(editingPoemId.value, {
        ...payload,
        version_no: editingVersion.value,
      })
      ElMessage.success('诗词已保存')
    }
    closeEditor()
    await loadPoems()
  } catch (error) {
    editorError.value = getErrorMessage(error)
  } finally {
    editorSaving.value = false
  }
}

async function publishPoem(poem: Poem): Promise<void> {
  actionPoemId.value = poem.id
  try {
    await adminApi.publishPoem(poem.id)
    ElMessage.success('诗词已发布')
    await loadPoems()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    actionPoemId.value = null
  }
}

async function unpublishPoem(poem: Poem): Promise<void> {
  actionPoemId.value = poem.id
  try {
    await adminApi.unpublishPoem(poem.id)
    ElMessage.success('诗词已撤回为草稿')
    await loadPoems()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    actionPoemId.value = null
  }
}

async function deletePoem(poem: Poem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除《${poem.title}》后，公开页面将不再显示；记录仍可在“包含已删除”中恢复。`,
      '删除诗词',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  actionPoemId.value = poem.id
  try {
    await adminApi.deletePoem(poem.id)
    ElMessage.success('诗词已删除')
    await loadPoems()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    actionPoemId.value = null
  }
}

async function restorePoem(poem: Poem): Promise<void> {
  actionPoemId.value = poem.id
  try {
    await adminApi.restorePoem(poem.id)
    ElMessage.success('诗词已恢复为草稿')
    await loadPoems()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    actionPoemId.value = null
  }
}

onMounted(() => {
  void loadOptions()
  void loadPoems()
})
</script>

<template>
  <div class="page page--admin admin-page">
    <header class="page-heading admin-heading">
      <div>
        <p class="section-kicker">内容管理</p>
        <h1>诗词管理</h1>
      </div>
      <AdminNav />
    </header>

    <form class="admin-toolbar" @submit.prevent="applyFilters">
      <label class="admin-search">
        <span class="sr-only">搜索诗词</span>
        <input v-model="filters.q" type="search" placeholder="搜索标题、作者、正文、分类或标签" />
      </label>
      <label>
        <span>状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="item in statusOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </option>
        </select>
      </label>
      <label>
        <span>作者</span>
        <select v-model.number="filters.authorId" :disabled="optionsLoading">
          <option value="">全部作者</option>
          <option v-for="author in authors" :key="author.id" :value="author.id">
            {{ author.name }}
          </option>
        </select>
      </label>
      <label>
        <span>朝代</span>
        <select v-model.number="filters.dynastyId" :disabled="optionsLoading">
          <option value="">全部朝代</option>
          <option v-for="dynasty in dynasties" :key="dynasty.id" :value="dynasty.id">
            {{ dynasty.name }}
          </option>
        </select>
      </label>
      <label class="admin-checkbox">
        <input v-model="filters.includeDeleted" type="checkbox" />
        <span>包含已删除</span>
      </label>
      <div class="admin-toolbar__actions">
        <button class="button button--secondary" type="button" @click="clearFilters">清除</button>
        <button class="button button--primary" type="submit">筛选</button>
      </div>
    </form>

    <p v-if="optionsError" class="form-error" role="alert">{{ optionsError }}</p>

    <div class="admin-table-meta">
      <p>共 {{ meta.total }} 首诗词</p>
      <button class="button button--primary" type="button" @click="openCreate">新建诗词</button>
    </div>

    <div class="admin-table-wrap">
      <table class="admin-table">
        <thead>
          <tr>
            <th scope="col">诗词</th>
            <th scope="col">作者与朝代</th>
            <th scope="col">分类与标签</th>
            <th scope="col">状态</th>
            <th scope="col">更新时间</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="isLoading">
            <td class="admin-table__empty" colspan="6">正在读取诗词...</td>
          </tr>
          <tr v-else-if="errorMessage">
            <td class="admin-table__empty" colspan="6">
              <strong>{{ errorMessage }}</strong>
              <button class="text-button" type="button" @click="loadPoems">重试</button>
            </td>
          </tr>
          <tr v-else-if="!poems.length">
            <td class="admin-table__empty" colspan="6">没有符合条件的诗词。</td>
          </tr>
          <tr
            v-for="poem in poems"
            v-else
            :key="poem.id"
            :class="{ 'admin-table__row--deleted': isDeleted(poem) }"
          >
            <td>
              <strong class="admin-table__title">{{ poem.title }}</strong>
              <span class="admin-table__id">#{{ poem.id }} · v{{ poem.version_no }}</span>
            </td>
            <td>
              <span>{{ poem.author_name || '未署作者' }}</span>
              <small>{{ poem.dynasty_name || '未标朝代' }}</small>
            </td>
            <td>
              <div class="admin-inline-list">
                <span v-for="category in poem.categories" :key="category.id">
                  {{ category.name }}
                </span>
                <span v-for="tag in poem.tags" :key="tag.id" class="admin-inline-list__tag">
                  {{ tag.name }}
                </span>
                <span v-if="!poem.categories.length && !poem.tags.length">未分类</span>
              </div>
            </td>
            <td>
              <span :class="statusClass(poem)">{{ rowStatusLabel(poem) }}</span>
            </td>
            <td>{{ formatDate(poem.updated_at) }}</td>
            <td>
              <div class="admin-row-actions">
                <template v-if="isDeleted(poem)">
                  <button
                    class="text-button"
                    type="button"
                    :disabled="actionPoemId === poem.id"
                    @click="restorePoem(poem)"
                  >
                    恢复
                  </button>
                </template>
                <template v-else>
                  <button class="text-button" type="button" @click="openEdit(poem)">编辑</button>
                  <button
                    v-if="poem.status !== 'published'"
                    class="text-button"
                    type="button"
                    :disabled="actionPoemId === poem.id"
                    @click="publishPoem(poem)"
                  >
                    发布
                  </button>
                  <button
                    v-else
                    class="text-button"
                    type="button"
                    :disabled="actionPoemId === poem.id"
                    @click="unpublishPoem(poem)"
                  >
                    撤回
                  </button>
                  <button
                    class="text-button text-button--danger"
                    type="button"
                    :disabled="actionPoemId === poem.id"
                    @click="deletePoem(poem)"
                  >
                    删除
                  </button>
                </template>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <nav v-if="meta.total_pages > 1" class="pagination" aria-label="管理列表分页">
      <button
        class="pagination__button"
        type="button"
        :disabled="currentPage <= 1 || isLoading"
        @click="goToPage(currentPage - 1)"
      >
        上一页
      </button>
      <span>第 {{ meta.page }} / {{ meta.total_pages }} 页</span>
      <button
        class="pagination__button"
        type="button"
        :disabled="currentPage >= meta.total_pages || isLoading"
        @click="goToPage(currentPage + 1)"
      >
        下一页
      </button>
    </nav>

    <div v-if="editorOpen" class="admin-dialog-backdrop" @click.self="closeEditor">
      <section
        class="admin-dialog admin-dialog--wide"
        role="dialog"
        aria-modal="true"
        :aria-label="editorTitle"
      >
        <header class="admin-dialog__header">
          <div>
            <p class="section-kicker">诗词内容</p>
            <h2>{{ editorTitle }}</h2>
          </div>
          <button class="admin-icon-button" type="button" aria-label="关闭" @click="closeEditor">
            ×
          </button>
        </header>

        <form class="admin-form" @submit.prevent="savePoem">
          <div v-if="editorLoading" class="admin-form__loading">正在读取诗词...</div>
          <template v-else>
            <label class="admin-form__span-2">
              <span>标题</span>
              <input v-model="form.title" type="text" maxlength="255" required />
            </label>
            <label>
              <span>作者</span>
              <select v-model.number="form.authorId">
                <option value="">未指定</option>
                <option v-for="author in authors" :key="author.id" :value="author.id">
                  {{ author.name }}
                </option>
              </select>
            </label>
            <label>
              <span>朝代</span>
              <select v-model.number="form.dynastyId">
                <option value="">未指定</option>
                <option v-for="dynasty in dynasties" :key="dynasty.id" :value="dynasty.id">
                  {{ dynasty.name }}
                </option>
              </select>
            </label>
            <label class="admin-form__span-2">
              <span>正文</span>
              <textarea v-model="form.content" rows="10" required></textarea>
            </label>
            <label class="admin-form__span-2">
              <span>摘要</span>
              <textarea v-model="form.summary" rows="3" maxlength="5000"></textarea>
            </label>
            <fieldset class="admin-form__span-2 admin-checkbox-grid">
              <legend>分类</legend>
              <label v-for="category in parentCategories" :key="category.id">
                <input v-model="form.categoryIds" type="checkbox" :value="category.id" />
                <span>{{ category.name }}</span>
              </label>
              <p v-if="!parentCategories.length">目录中暂无可用分类。</p>
            </fieldset>
            <label class="admin-form__span-2">
              <span>标签</span>
              <input v-model="form.tagText" type="text" placeholder="多个标签用逗号分隔" />
            </label>
          </template>

          <p v-if="editorError" class="form-error admin-form__span-2" role="alert">
            {{ editorError }}
          </p>
          <div class="admin-dialog__actions admin-form__span-2">
            <button class="button button--secondary" type="button" @click="closeEditor">
              取消
            </button>
            <button
              class="button button--primary"
              type="submit"
              :disabled="editorLoading || editorSaving"
            >
              {{ editorSaving ? '保存中...' : editorSubmitLabel }}
            </button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>