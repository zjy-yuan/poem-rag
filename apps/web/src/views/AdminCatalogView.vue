<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import AdminNav from '@/components/AdminNav.vue'
import { adminApi } from '@/api/admin'
import { getErrorMessage } from '@/api/http'
import type {
  Author,
  CategoryType,
  Dynasty,
  PaginationMeta,
  PoemCategory,
} from '@/types/api'

type CatalogSection = 'authors' | 'dynasties' | 'categories'

const sectionOptions: Array<{ value: CatalogSection; label: string }> = [
  { value: 'authors', label: '作者' },
  { value: 'dynasties', label: '朝代' },
  { value: 'categories', label: '分类' },
]

const categoryTypes: Array<{ value: CategoryType; label: string }> = [
  { value: 'work_type', label: '体裁' },
  { value: 'form', label: '形式' },
  { value: 'style', label: '风格' },
  { value: 'theme', label: '主题' },
]

const activeSection = ref<CatalogSection>('authors')
const authors = ref<Author[]>([])
const dynasties = ref<Dynasty[]>([])
const categories = ref<PoemCategory[]>([])
const authorQuery = ref('')
const authorMeta = ref<PaginationMeta>({ page: 1, page_size: 100, total: 0, total_pages: 0 })
const isLoading = ref(true)
const errorMessage = ref('')
const editorOpen = ref(false)
const editorSection = ref<CatalogSection>('authors')
const editingId = ref<number | null>(null)
const editorSaving = ref(false)
const editorError = ref('')

const authorForm = reactive({
  name: '',
  aliasesText: '',
  bio: '',
  dynastyId: '' as number | '',
})
const dynastyForm = reactive({
  name: '',
  description: '',
  sortOrder: 0,
})
const categoryForm = reactive({
  name: '',
  type: 'work_type' as CategoryType,
  parentId: '' as number | '',
  sortOrder: 0,
  isActive: true,
})

const activeDynasties = computed(() => dynasties.value)
const parentCategories = computed(() =>
  categories.value.filter(
    (item) => item.type === categoryForm.type && item.id !== editingId.value && item.is_active,
  ),
)
const editorTitle = computed(() => {
  const labels: Record<CatalogSection, string> = {
    authors: '作者',
    dynasties: '朝代',
    categories: '分类',
  }
  return `${editingId.value === null ? '新建' : '编辑'}${labels[editorSection.value]}`
})
const currentCount = computed(() => {
  if (activeSection.value === 'authors') {
    return authorMeta.value.total
  }
  if (activeSection.value === 'dynasties') {
    return dynasties.value.length
  }
  return categories.value.length
})
const addLabel = computed(() => {
  const labels: Record<CatalogSection, string> = {
    authors: '新建作者',
    dynasties: '新建朝代',
    categories: '新建分类',
  }
  return labels[activeSection.value]
})

function categoryTypeLabel(type: CategoryType): string {
  return categoryTypes.find((item) => item.value === type)?.label ?? type
}

function categoryParentName(parentId: number | null): string {
  if (parentId === null) {
    return '无'
  }
  return categories.value.find((item) => item.id === parentId)?.name ?? `#${parentId}`
}

function parseLabels(value: string): string[] {
  const seen = new Set<string>()
  return value
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

async function loadAuthors(): Promise<void> {
  const result = await adminApi.listAuthors({
    page: 1,
    page_size: 100,
    q: authorQuery.value.trim() || undefined,
  })
  authors.value = result.items
  authorMeta.value = result.meta
}

async function loadDynasties(): Promise<void> {
  dynasties.value = await adminApi.listDynasties()
}

async function loadCategories(): Promise<void> {
  categories.value = await adminApi.listCategories()
}


async function loadAll(): Promise<void> {
  isLoading.value = true
  errorMessage.value = ''
  try {
    await Promise.all([loadAuthors(), loadDynasties(), loadCategories()])
  } catch (error) {
    errorMessage.value = getErrorMessage(error)
  } finally {
    isLoading.value = false
  }
}

async function selectSection(section: CatalogSection): Promise<void> {
  activeSection.value = section
  errorMessage.value = ''
  if (section === 'authors' && !authors.value.length) {
    await loadAuthors()
  }
}

async function submitAuthorSearch(): Promise<void> {
  try {
    await loadAuthors()
  } catch (error) {
    errorMessage.value = getErrorMessage(error)
  }
}

function resetAuthorForm(): void {
  authorForm.name = ''
  authorForm.aliasesText = ''
  authorForm.bio = ''
  authorForm.dynastyId = ''
}

function resetDynastyForm(): void {
  dynastyForm.name = ''
  dynastyForm.description = ''
  dynastyForm.sortOrder = dynasties.value.length * 10
}

function resetCategoryForm(): void {
  categoryForm.name = ''
  categoryForm.type = 'work_type'
  categoryForm.parentId = ''
  categoryForm.sortOrder = 0
  categoryForm.isActive = true
}

function openCreate(): void {
  editingId.value = null
  editorError.value = ''
  editorSection.value = activeSection.value
  if (activeSection.value === 'authors') {
    resetAuthorForm()
  } else if (activeSection.value === 'dynasties') {
    resetDynastyForm()
  } else {
    resetCategoryForm()
  }
  editorOpen.value = true
}

function openEditAuthor(author: Author): void {
  editingId.value = author.id
  editorSection.value = 'authors'
  editorError.value = ''
  authorForm.name = author.name
  authorForm.aliasesText = author.aliases.join(', ')
  authorForm.bio = author.bio ?? ''
  authorForm.dynastyId = author.dynasty_id ?? ''
  editorOpen.value = true
}

function openEditDynasty(dynasty: Dynasty): void {
  editingId.value = dynasty.id
  editorSection.value = 'dynasties'
  editorError.value = ''
  dynastyForm.name = dynasty.name
  dynastyForm.description = dynasty.description ?? ''
  dynastyForm.sortOrder = dynasty.sort_order
  editorOpen.value = true
}

function openEditCategory(category: PoemCategory): void {
  editingId.value = category.id
  editorSection.value = 'categories'
  editorError.value = ''
  categoryForm.name = category.name
  categoryForm.type = category.type
  categoryForm.parentId = category.parent_id ?? ''
  categoryForm.sortOrder = category.sort_order
  categoryForm.isActive = category.is_active
  editorOpen.value = true
}

function closeEditor(): void {
  if (editorSaving.value) {
    return
  }
  editorOpen.value = false
  editingId.value = null
  editorError.value = ''
}

async function saveAuthor(): Promise<void> {
  if (!authorForm.name.trim()) {
    editorError.value = '作者名称不能为空。'
    return
  }
  const payload = {
    name: authorForm.name.trim(),
    aliases: parseLabels(authorForm.aliasesText),
    bio: authorForm.bio.trim() || null,
    dynasty_id: authorForm.dynastyId === '' ? null : authorForm.dynastyId,
  }
  editorSaving.value = true
  editorError.value = ''
  try {
    if (editingId.value === null) {
      await adminApi.createAuthor(payload)
      ElMessage.success('作者已创建')
    } else {
      await adminApi.updateAuthor(editingId.value, payload)
      ElMessage.success('作者已保存')
    }
    closeEditor()
    await loadAuthors()
  } catch (error) {
    editorError.value = getErrorMessage(error)
  } finally {
    editorSaving.value = false
  }
}

async function saveDynasty(): Promise<void> {
  if (!dynastyForm.name.trim()) {
    editorError.value = '朝代名称不能为空。'
    return
  }
  const payload = {
    name: dynastyForm.name.trim(),
    description: dynastyForm.description.trim() || null,
    sort_order: dynastyForm.sortOrder,
  }
  editorSaving.value = true
  editorError.value = ''
  try {
    if (editingId.value === null) {
      await adminApi.createDynasty(payload)
      ElMessage.success('朝代已创建')
    } else {
      await adminApi.updateDynasty(editingId.value, payload)
      ElMessage.success('朝代已保存')
    }
    closeEditor()
    await Promise.all([loadDynasties(), loadAuthors()])
  } catch (error) {
    editorError.value = getErrorMessage(error)
  } finally {
    editorSaving.value = false
  }
}

async function saveCategory(): Promise<void> {
  if (!categoryForm.name.trim()) {
    editorError.value = '分类名称不能为空。'
    return
  }
  const payload = {
    name: categoryForm.name.trim(),
    type: categoryForm.type,
    parent_id: categoryForm.parentId === '' ? null : categoryForm.parentId,
    sort_order: categoryForm.sortOrder,
    is_active: categoryForm.isActive,
  }
  editorSaving.value = true
  editorError.value = ''
  try {
    if (editingId.value === null) {
      await adminApi.createCategory(payload)
      ElMessage.success('分类已创建')
    } else {
      await adminApi.updateCategory(editingId.value, payload)
      ElMessage.success('分类已保存')
    }
    closeEditor()
    await loadCategories()
  } catch (error) {
    editorError.value = getErrorMessage(error)
  } finally {
    editorSaving.value = false
  }
}

async function saveEditor(): Promise<void> {
  if (editorSection.value === 'authors') {
    await saveAuthor()
  } else if (editorSection.value === 'dynasties') {
    await saveDynasty()
  } else {
    await saveCategory()
  }
}

async function deleteAuthor(author: Author): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除作者“${author.name}”？`, '删除作者', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await adminApi.deleteAuthor(author.id)
    ElMessage.success('作者已删除')
    await loadAuthors()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

async function deleteDynasty(dynasty: Dynasty): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除朝代“${dynasty.name}”？`, '删除朝代', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await adminApi.deleteDynasty(dynasty.id)
    ElMessage.success('朝代已删除')
    await loadDynasties()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

async function setCategoryActive(category: PoemCategory, active: boolean): Promise<void> {
  try {
    if (active) {
      await adminApi.updateCategory(category.id, { is_active: true })
      ElMessage.success('分类已启用')
    } else {
      await adminApi.disableCategory(category.id)
      ElMessage.success('分类已停用')
    }
    await loadCategories()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

onMounted(() => {
  void loadAll()
})
</script>

<template>
  <div class="page page--admin admin-page">
    <header class="page-heading admin-heading">
      <div>
        <p class="section-kicker">内容基础</p>
        <h1>目录管理</h1>
      </div>
      <AdminNav />
    </header>

    <div class="admin-section-tabs" role="tablist" aria-label="目录类型">
      <button
        v-for="item in sectionOptions"
        :key="item.value"
        type="button"
        role="tab"
        :aria-selected="activeSection === item.value"
        :class="{ 'admin-section-tabs__button--active': activeSection === item.value }"
        @click="selectSection(item.value)"
      >
        {{ item.label }}
      </button>
    </div>

    <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>

    <div class="admin-table-meta">
      <p>
        <template v-if="activeSection === 'authors'">共 {{ currentCount }} 位作者</template>
        <template v-else-if="activeSection === 'dynasties'">共 {{ currentCount }} 个朝代</template>
        <template v-else>共 {{ currentCount }} 个分类</template>
      </p>
      <button class="button button--primary" type="button" @click="openCreate">
        {{ addLabel }}
      </button>
    </div>

    <section v-if="activeSection === 'authors'" class="admin-section-panel" role="tabpanel">
      <form class="admin-inline-filter" @submit.prevent="submitAuthorSearch">
        <input v-model="authorQuery" type="search" placeholder="按作者名称搜索" />
        <button class="button button--secondary" type="submit">搜索</button>
      </form>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th scope="col">作者</th>
              <th scope="col">别名</th>
              <th scope="col">朝代</th>
              <th scope="col">诗词数</th>
              <th scope="col">简介</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="isLoading"><td class="admin-table__empty" colspan="6">正在读取...</td></tr>
            <tr v-else-if="!authors.length"><td class="admin-table__empty" colspan="6">暂无作者。</td></tr>
            <tr v-for="author in authors" v-else :key="author.id">
              <td><strong>{{ author.name }}</strong></td>
              <td>{{ author.aliases.join('、') || '无' }}</td>
              <td>{{ author.dynasty_name || '未标朝代' }}</td>
              <td>{{ author.poem_count }}</td>
              <td class="admin-table__clamp">{{ author.bio || '暂无简介' }}</td>
              <td>
                <div class="admin-row-actions">
                  <button class="text-button" type="button" @click="openEditAuthor(author)">编辑</button>
                  <button class="text-button text-button--danger" type="button" @click="deleteAuthor(author)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-else-if="activeSection === 'dynasties'" class="admin-section-panel" role="tabpanel">
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th scope="col">朝代</th>
              <th scope="col">说明</th>
              <th scope="col">排序</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="isLoading"><td class="admin-table__empty" colspan="4">正在读取...</td></tr>
            <tr v-else-if="!dynasties.length"><td class="admin-table__empty" colspan="4">暂无朝代。</td></tr>
            <tr v-for="dynasty in dynasties" v-else :key="dynasty.id">
              <td><strong>{{ dynasty.name }}</strong></td>
              <td class="admin-table__clamp">{{ dynasty.description || '暂无说明' }}</td>
              <td>{{ dynasty.sort_order }}</td>
              <td>
                <div class="admin-row-actions">
                  <button class="text-button" type="button" @click="openEditDynasty(dynasty)">编辑</button>
                  <button class="text-button text-button--danger" type="button" @click="deleteDynasty(dynasty)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-else class="admin-section-panel" role="tabpanel">
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th scope="col">分类</th>
              <th scope="col">类型</th>
              <th scope="col">上级分类</th>
              <th scope="col">排序</th>
              <th scope="col">状态</th>
              <th scope="col">诗词数</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="isLoading"><td class="admin-table__empty" colspan="7">正在读取...</td></tr>
            <tr v-else-if="!categories.length"><td class="admin-table__empty" colspan="7">暂无分类。</td></tr>
            <tr v-for="category in categories" v-else :key="category.id">
              <td><strong>{{ category.name }}</strong></td>
              <td>{{ categoryTypeLabel(category.type) }}</td>
              <td>{{ categoryParentName(category.parent_id) }}</td>
              <td>{{ category.sort_order }}</td>
              <td>
                <span :class="['admin-status', category.is_active ? 'admin-status--published' : 'admin-status--deleted']">
                  {{ category.is_active ? '启用' : '停用' }}
                </span>
              </td>
              <td>{{ category.poem_count }}</td>
              <td>
                <div class="admin-row-actions">
                  <button class="text-button" type="button" @click="openEditCategory(category)">编辑</button>
                  <button
                    v-if="category.is_active"
                    class="text-button text-button--danger"
                    type="button"
                    @click="setCategoryActive(category, false)"
                  >
                    停用
                  </button>
                  <button v-else class="text-button" type="button" @click="setCategoryActive(category, true)">
                    启用
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div v-if="editorOpen" class="admin-dialog-backdrop" @click.self="closeEditor">
      <section class="admin-dialog" role="dialog" aria-modal="true" :aria-label="editorTitle">
        <header class="admin-dialog__header">
          <div>
            <p class="section-kicker">目录资料</p>
            <h2>{{ editorTitle }}</h2>
          </div>
          <button class="admin-icon-button" type="button" aria-label="关闭" @click="closeEditor">×</button>
        </header>

        <form class="admin-form" @submit.prevent="saveEditor">
          <template v-if="editorSection === 'authors'">
            <label class="admin-form__span-2">
              <span>作者名称</span>
              <input v-model="authorForm.name" type="text" maxlength="120" required />
            </label>
            <label class="admin-form__span-2">
              <span>别名</span>
              <input v-model="authorForm.aliasesText" type="text" placeholder="多个别名用逗号分隔" />
            </label>
            <label>
              <span>朝代</span>
              <select v-model.number="authorForm.dynastyId">
                <option value="">未指定</option>
                <option v-for="dynasty in activeDynasties" :key="dynasty.id" :value="dynasty.id">
                  {{ dynasty.name }}
                </option>
              </select>
            </label>
            <label class="admin-form__span-2">
              <span>简介</span>
              <textarea v-model="authorForm.bio" rows="5" maxlength="5000"></textarea>
            </label>
          </template>

          <template v-else-if="editorSection === 'dynasties'">
            <label class="admin-form__span-2">
              <span>朝代名称</span>
              <input v-model="dynastyForm.name" type="text" maxlength="80" required />
            </label>
            <label>
              <span>排序</span>
              <input v-model.number="dynastyForm.sortOrder" type="number" min="0" max="100000" />
            </label>
            <label class="admin-form__span-2">
              <span>说明</span>
              <textarea v-model="dynastyForm.description" rows="4" maxlength="2000"></textarea>
            </label>
          </template>

          <template v-else>
            <label>
              <span>分类名称</span>
              <input v-model="categoryForm.name" type="text" maxlength="80" required />
            </label>
            <label>
              <span>分类类型</span>
              <select v-model="categoryForm.type">
                <option v-for="item in categoryTypes" :key="item.value" :value="item.value">
                  {{ item.label }}
                </option>
              </select>
            </label>
            <label>
              <span>上级分类</span>
              <select v-model.number="categoryForm.parentId">
                <option value="">无</option>
                <option v-for="category in parentCategories" :key="category.id" :value="category.id">
                  {{ category.name }}
                </option>
              </select>
            </label>
            <label>
              <span>排序</span>
              <input v-model.number="categoryForm.sortOrder" type="number" min="0" max="100000" />
            </label>
            <label class="admin-checkbox admin-form__span-2">
              <input v-model="categoryForm.isActive" type="checkbox" />
              <span>启用该分类</span>
            </label>
          </template>

          <p v-if="editorError" class="form-error admin-form__span-2" role="alert">
            {{ editorError }}
          </p>
          <div class="admin-dialog__actions admin-form__span-2">
            <button class="button button--secondary" type="button" @click="closeEditor">取消</button>
            <button class="button button--primary" type="submit" :disabled="editorSaving">
              {{ editorSaving ? '保存中...' : '保存' }}
            </button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>