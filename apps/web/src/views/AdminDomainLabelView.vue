<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import AdminNav from '@/components/AdminNav.vue'
import {
  adminApi,
  type DomainLabelUpdatePayload,
} from '@/api/admin'
import { getErrorMessage } from '@/api/http'
import type {
  DomainLabel,
  DomainLabelAssignment,
  DomainLabelDimension,
  DomainLabelGenerationMethod,
  DomainLabelReviewAction,
  DomainLabelReviewStatus,
  DomainLabelStatus,
  PaginationMeta,
} from '@/types/api'

type DomainLabelSection = 'catalog' | 'review'

interface ReviewActionItem {
  action: DomainLabelReviewAction
  label: string
  danger?: boolean
}

const sectionOptions: Array<{ value: DomainLabelSection; label: string }> = [
  { value: 'catalog', label: '标签库' },
  { value: 'review', label: '关联审核' },
]

const dimensionOptions: Array<{ value: DomainLabelDimension; label: string }> = [
  { value: 'imagery', label: '意象' },
  { value: 'emotion', label: '情感' },
  { value: 'theme', label: '题材' },
  { value: 'allusion', label: '典故' },
]

const labelStatusOptions: Array<{ value: DomainLabelStatus; label: string }> = [
  { value: 'active', label: '启用' },
  { value: 'merged', label: '已合并' },
  { value: 'deprecated', label: '已废弃' },
]

const reviewStatusOptions: Array<{ value: DomainLabelReviewStatus; label: string }> = [
  { value: 'pending', label: '待审核' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已驳回' },
  { value: 'archived', label: '已归档' },
]

const generationOptions: Array<{ value: DomainLabelGenerationMethod; label: string }> = [
  { value: 'manual', label: '人工标注' },
  { value: 'public_dataset', label: '公开数据集' },
  { value: 'ai', label: 'AI 生成' },
]

const activeSection = ref<DomainLabelSection>('catalog')
const labels = ref<DomainLabel[]>([])
const labelOptions = ref<DomainLabel[]>([])
const assignments = ref<DomainLabelAssignment[]>([])
const labelMeta = ref<PaginationMeta>({ page: 1, page_size: 20, total: 0, total_pages: 0 })
const assignmentMeta = ref<PaginationMeta>({
  page: 1,
  page_size: 20,
  total: 0,
  total_pages: 0,
})
const labelPage = ref(1)
const assignmentPage = ref(1)
const labelsLoading = ref(true)
const labelOptionsLoading = ref(false)
const assignmentsLoading = ref(false)
const labelsError = ref('')
const assignmentsError = ref('')
const rowActionId = ref<number | null>(null)

const labelFilters = reactive({
  q: '',
  dimension: '' as DomainLabelDimension | '',
  status: '' as DomainLabelStatus | '',
})

const assignmentFilters = reactive({
  reviewStatus: '' as DomainLabelReviewStatus | '',
  generationMethod: '' as DomainLabelGenerationMethod | '',
})

const assignmentLabelQuery = ref('')
const assignmentLabelId = ref<number | ''>('')

const editorOpen = ref(false)
const editingLabelId = ref<number | null>(null)
const editorSaving = ref(false)
const editorError = ref('')
const editorForm = reactive({
  dimension: 'imagery' as DomainLabelDimension,
  canonicalName: '',
  description: '',
  aliasesText: '',
})

const mergeOpen = ref(false)
const mergeSource = ref<DomainLabel | null>(null)
const mergeTargetId = ref<number | ''>('')
const mergeTargets = ref<DomainLabel[]>([])
const mergeLoading = ref(false)
const mergeSaving = ref(false)
const mergeError = ref('')

let labelRequestGeneration = 0
let assignmentRequestGeneration = 0

const editorTitle = computed(() =>
  editingLabelId.value === null ? '新建领域标签' : '编辑领域标签',
)
const selectedAssignmentLabel = computed(() => {
  if (assignmentLabelId.value === '') {
    return null
  }
  return (
    labelOptions.value.find((item) => item.id === assignmentLabelId.value) ??
    labels.value.find((item) => item.id === assignmentLabelId.value) ??
    null
  )
})
const mergeTargetOptions = computed(() =>
  mergeTargets.value.filter((item) => item.id !== mergeSource.value?.id),
)

function dimensionLabel(value: DomainLabelDimension): string {
  return dimensionOptions.find((item) => item.value === value)?.label ?? value
}

function labelStatusLabel(value: DomainLabelStatus): string {
  return labelStatusOptions.find((item) => item.value === value)?.label ?? value
}

function reviewStatusLabel(value: DomainLabelReviewStatus): string {
  return reviewStatusOptions.find((item) => item.value === value)?.label ?? value
}

function generationMethodLabel(value: DomainLabelGenerationMethod): string {
  return generationOptions.find((item) => item.value === value)?.label ?? value
}

function labelStatusClass(value: DomainLabelStatus): string {
  if (value === 'active') {
    return 'admin-status admin-status--published'
  }
  if (value === 'merged') {
    return 'admin-status admin-status--draft'
  }
  return 'admin-status admin-status--archived'
}

function reviewStatusClass(value: DomainLabelReviewStatus): string {
  if (value === 'approved') {
    return 'admin-status admin-status--published'
  }
  if (value === 'pending') {
    return 'admin-status admin-status--draft'
  }
  return 'admin-status admin-status--archived'
}

function formatDate(value: string | null): string {
  if (!value) {
    return '未记录'
  }
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function formatConfidence(value: number | null): string {
  if (value === null) {
    return '未记录'
  }
  return `${Math.round(value * 100)}%`
}

function formatLineRange(item: DomainLabelAssignment): string {
  if (item.line_start === null && item.line_end === null) {
    return '未标行号'
  }
  if (item.line_start === item.line_end || item.line_end === null) {
    return `第 ${item.line_start} 行`
  }
  if (item.line_start === null) {
    return `第 ${item.line_end} 行`
  }
  return `第 ${item.line_start}-${item.line_end} 行`
}

function mergedLabelName(label: DomainLabel): string {
  if (label.merged_into_id === null) {
    return '未指定'
  }
  return (
    labels.value.find((item) => item.id === label.merged_into_id)?.canonical_name ??
    labelOptions.value.find((item) => item.id === label.merged_into_id)?.canonical_name ??
    `#${label.merged_into_id}`
  )
}

function parseAliases(value: string): string[] {
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

function reviewActions(item: DomainLabelAssignment): ReviewActionItem[] {
  if (item.review_status === 'pending') {
    return [
      { action: 'approve', label: '通过' },
      { action: 'reject', label: '驳回', danger: true },
    ]
  }
  if (item.review_status === 'approved') {
    return [{ action: 'archive', label: '归档', danger: true }]
  }
  if (item.review_status === 'rejected') {
    return [{ action: 'reassess', label: '重新评估' }]
  }
  return []
}

function mergeLabelOptions(items: DomainLabel[]): void {
  const merged = new Map<number, DomainLabel>()
  const selected = selectedAssignmentLabel.value
  if (selected) {
    merged.set(selected.id, selected)
  }
  for (const item of labelOptions.value) {
    merged.set(item.id, item)
  }
  for (const item of items) {
    merged.set(item.id, item)
  }
  labelOptions.value = [...merged.values()].sort((left, right) =>
    left.canonical_name.localeCompare(right.canonical_name, 'zh-CN'),
  )
}

async function loadLabels(): Promise<void> {
  const generation = ++labelRequestGeneration
  labelsLoading.value = true
  labelsError.value = ''
  try {
    const result = await adminApi.listDomainLabels({
      page: labelPage.value,
      page_size: labelMeta.value.page_size,
      q: labelFilters.q.trim() || undefined,
      dimension: labelFilters.dimension || undefined,
      status: labelFilters.status || undefined,
    })
    if (generation !== labelRequestGeneration) {
      return
    }
    labels.value = result.items
    labelMeta.value = result.meta
    mergeLabelOptions(result.items)
  } catch (error) {
    if (generation !== labelRequestGeneration) {
      return
    }
    labels.value = []
    labelsError.value = getErrorMessage(error)
  } finally {
    if (generation === labelRequestGeneration) {
      labelsLoading.value = false
    }
  }
}

async function loadLabelOptions(): Promise<void> {
  labelOptionsLoading.value = true
  assignmentsError.value = ''
  try {
    const result = await adminApi.listDomainLabels({
      page: 1,
      page_size: 100,
      q: assignmentLabelQuery.value.trim() || undefined,
    })
    mergeLabelOptions(result.items)
    if (
      assignmentLabelId.value === '' ||
      !labelOptions.value.some((item) => item.id === assignmentLabelId.value)
    ) {
      assignmentLabelId.value = result.items[0]?.id ?? ''
    }
  } catch (error) {
    assignmentsError.value = getErrorMessage(error)
  } finally {
    labelOptionsLoading.value = false
  }
}

async function loadAssignments(): Promise<void> {
  if (assignmentLabelId.value === '') {
    assignments.value = []
    assignmentMeta.value = {
      page: 1,
      page_size: assignmentMeta.value.page_size,
      total: 0,
      total_pages: 0,
    }
    return
  }

  const generation = ++assignmentRequestGeneration
  assignmentsLoading.value = true
  assignmentsError.value = ''
  try {
    const result = await adminApi.listDomainLabelAssignments(assignmentLabelId.value, {
      page: assignmentPage.value,
      page_size: assignmentMeta.value.page_size,
      review_status: assignmentFilters.reviewStatus || undefined,
      generation_method: assignmentFilters.generationMethod || undefined,
    })
    if (generation !== assignmentRequestGeneration) {
      return
    }
    assignments.value = result.items
    assignmentMeta.value = result.meta
  } catch (error) {
    if (generation !== assignmentRequestGeneration) {
      return
    }
    assignments.value = []
    assignmentsError.value = getErrorMessage(error)
  } finally {
    if (generation === assignmentRequestGeneration) {
      assignmentsLoading.value = false
    }
  }
}

async function selectSection(section: DomainLabelSection): Promise<void> {
  activeSection.value = section
  if (section !== 'review') {
    return
  }
  if (!labelOptions.value.length) {
    await loadLabelOptions()
  }
  await loadAssignments()
}

async function applyLabelFilters(): Promise<void> {
  labelPage.value = 1
  await loadLabels()
}

async function clearLabelFilters(): Promise<void> {
  labelFilters.q = ''
  labelFilters.dimension = ''
  labelFilters.status = ''
  labelPage.value = 1
  await loadLabels()
}

async function goToLabelPage(page: number): Promise<void> {
  if (page < 1 || page > labelMeta.value.total_pages || page === labelPage.value) {
    return
  }
  labelPage.value = page
  await loadLabels()
}

async function searchAssignmentLabels(): Promise<void> {
  await loadLabelOptions()
  assignmentPage.value = 1
  await loadAssignments()
}

async function changeAssignmentLabel(): Promise<void> {
  assignmentPage.value = 1
  await loadAssignments()
}

async function applyAssignmentFilters(): Promise<void> {
  assignmentPage.value = 1
  await loadAssignments()
}

async function clearAssignmentFilters(): Promise<void> {
  assignmentFilters.reviewStatus = ''
  assignmentFilters.generationMethod = ''
  assignmentPage.value = 1
  await loadAssignments()
}

async function goToAssignmentPage(page: number): Promise<void> {
  if (
    page < 1 ||
    page > assignmentMeta.value.total_pages ||
    page === assignmentPage.value
  ) {
    return
  }
  assignmentPage.value = page
  await loadAssignments()
}

function resetEditorForm(): void {
  editorForm.dimension = 'imagery'
  editorForm.canonicalName = ''
  editorForm.description = ''
  editorForm.aliasesText = ''
  editorError.value = ''
}

function openCreate(): void {
  editingLabelId.value = null
  resetEditorForm()
  editorOpen.value = true
}

function openEdit(label: DomainLabel): void {
  editingLabelId.value = label.id
  editorForm.dimension = label.dimension
  editorForm.canonicalName = label.canonical_name
  editorForm.description = label.description ?? ''
  editorForm.aliasesText = label.aliases.map((item) => item.alias).join(', ')
  editorError.value = ''
  editorOpen.value = true
}

function closeEditor(): void {
  if (editorSaving.value) {
    return
  }
  editorOpen.value = false
  editingLabelId.value = null
  editorError.value = ''
}

async function saveLabel(): Promise<void> {
  const canonicalName = editorForm.canonicalName.trim()
  if (!canonicalName) {
    editorError.value = '标签名称不能为空。'
    return
  }

  editorSaving.value = true
  editorError.value = ''
  try {
    if (editingLabelId.value === null) {
      await adminApi.createDomainLabel({
        dimension: editorForm.dimension,
        canonical_name: canonicalName,
        description: editorForm.description.trim() || null,
        aliases: parseAliases(editorForm.aliasesText),
      })
      ElMessage.success('领域标签已创建')
    } else {
      const payload: DomainLabelUpdatePayload = {
        canonical_name: canonicalName,
        description: editorForm.description.trim() || null,
        aliases: parseAliases(editorForm.aliasesText),
      }
      await adminApi.updateDomainLabel(editingLabelId.value, payload)
      ElMessage.success('领域标签已保存')
    }
    closeEditor()
    await Promise.all([loadLabels(), loadLabelOptions()])
    if (activeSection.value === 'review') {
      await loadAssignments()
    }
  } catch (error) {
    editorError.value = getErrorMessage(error)
  } finally {
    editorSaving.value = false
  }
}

async function setLabelStatus(
  label: DomainLabel,
  status: DomainLabelStatus,
  successMessage: string,
): Promise<void> {
  rowActionId.value = label.id
  try {
    await adminApi.updateDomainLabel(label.id, { status })
    ElMessage.success(successMessage)
    await Promise.all([loadLabels(), loadLabelOptions()])
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    rowActionId.value = null
  }
}

async function deprecateLabel(label: DomainLabel): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `废弃“${label.canonical_name}”后，新关联不能继续使用该标签；历史关联仍会保留。`,
      '废弃领域标签',
      {
        type: 'warning',
        confirmButtonText: '废弃',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  await setLabelStatus(label, 'deprecated', '领域标签已废弃')
}

async function reactivateLabel(label: DomainLabel): Promise<void> {
  await setLabelStatus(label, 'active', '领域标签已重新启用')
}

async function loadMergeTargets(): Promise<void> {
  if (!mergeSource.value) {
    return
  }
  mergeLoading.value = true
  mergeError.value = ''
  try {
    const result = await adminApi.listDomainLabels({
      page: 1,
      page_size: 100,
      dimension: mergeSource.value.dimension,
      status: 'active',
    })
    mergeTargets.value = result.items.filter((item) => item.id !== mergeSource.value?.id)
  } catch (error) {
    mergeTargets.value = []
    mergeError.value = getErrorMessage(error)
  } finally {
    mergeLoading.value = false
  }
}

function openMerge(label: DomainLabel): void {
  mergeSource.value = label
  mergeTargetId.value = ''
  mergeTargets.value = []
  mergeError.value = ''
  mergeOpen.value = true
  void loadMergeTargets()
}

function closeMerge(): void {
  if (mergeSaving.value) {
    return
  }
  mergeOpen.value = false
  mergeSource.value = null
  mergeTargetId.value = ''
  mergeTargets.value = []
  mergeError.value = ''
}

async function saveMerge(): Promise<void> {
  if (!mergeSource.value || mergeTargetId.value === '') {
    mergeError.value = '请选择合并目标标签。'
    return
  }
  mergeSaving.value = true
  mergeError.value = ''
  try {
    await adminApi.updateDomainLabel(mergeSource.value.id, {
      status: 'merged',
      merged_into_id: mergeTargetId.value,
    })
    ElMessage.success('领域标签已合并')
    closeMerge()
    await Promise.all([loadLabels(), loadLabelOptions()])
  } catch (error) {
    mergeError.value = getErrorMessage(error)
  } finally {
    mergeSaving.value = false
  }
}

function viewLabelAssignments(label: DomainLabel): void {
  mergeLabelOptions([label])
  assignmentLabelId.value = label.id
  assignmentPage.value = 1
  activeSection.value = 'review'
  void loadAssignments()
}

async function reviewAssignment(
  item: DomainLabelAssignment,
  action: DomainLabelReviewAction,
): Promise<void> {
  const actionLabels: Record<DomainLabelReviewAction, string> = {
    approve: '通过',
    reject: '驳回',
    archive: '归档',
    reassess: '重新评估',
  }
  if (action === 'reject' || action === 'archive') {
    try {
      await ElMessageBox.confirm(
        `确认${actionLabels[action]}作品 #${item.poem_id} 的“${item.label.canonical_name}”关联？`,
        `${actionLabels[action]}关联`,
        {
          type: 'warning',
          confirmButtonText: actionLabels[action],
          cancelButtonText: '取消',
        },
      )
    } catch {
      return
    }
  }

  rowActionId.value = item.id
  try {
    await adminApi.reviewDomainLabelAssignment(item.id, action)
    ElMessage.success(`关联已${actionLabels[action]}`)
    await loadAssignments()
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    rowActionId.value = null
  }
}

onMounted(() => {
  void Promise.all([loadLabels(), loadLabelOptions()])
})
</script>

<template>
  <div class="page page--admin admin-page">
    <header class="page-heading admin-heading">
      <div>
        <p class="section-kicker">语义治理</p>
        <h1>领域标签</h1>
      </div>
      <AdminNav />
    </header>

    <div class="admin-section-tabs" role="tablist" aria-label="领域标签工作区">
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

    <template v-if="activeSection === 'catalog'">
      <form class="admin-toolbar admin-toolbar--domain" @submit.prevent="applyLabelFilters">
        <label class="admin-search">
          <span class="sr-only">搜索领域标签</span>
          <input v-model="labelFilters.q" type="search" placeholder="搜索标签名称或别名" />
        </label>
        <label>
          <span>维度</span>
          <select v-model="labelFilters.dimension">
            <option value="">全部维度</option>
            <option v-for="item in dimensionOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <label>
          <span>状态</span>
          <select v-model="labelFilters.status">
            <option value="">全部状态</option>
            <option v-for="item in labelStatusOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <div class="admin-toolbar__actions">
          <button class="button button--secondary" type="button" @click="clearLabelFilters">
            清除
          </button>
          <button class="button button--primary" type="submit">筛选</button>
        </div>
      </form>

      <p v-if="labelsError" class="form-error" role="alert">{{ labelsError }}</p>

      <div class="admin-table-meta">
        <p>共 {{ labelMeta.total }} 个领域标签</p>
        <button class="button button--primary" type="button" @click="openCreate">
          新建标签
        </button>
      </div>

      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead>
            <tr>
              <th scope="col">标签</th>
              <th scope="col">维度</th>
              <th scope="col">别名</th>
              <th scope="col">状态</th>
              <th scope="col">合并指向</th>
              <th scope="col">更新时间</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="labelsLoading">
              <td class="admin-table__empty" colspan="7">正在读取领域标签...</td>
            </tr>
            <tr v-else-if="labelsError">
              <td class="admin-table__empty" colspan="7">
                <strong>{{ labelsError }}</strong>
                <button class="text-button" type="button" @click="loadLabels">重试</button>
              </td>
            </tr>
            <tr v-else-if="!labels.length">
              <td class="admin-table__empty" colspan="7">没有符合条件的领域标签。</td>
            </tr>
            <tr v-for="label in labels" v-else :key="label.id">
              <td>
                <strong class="admin-table__title">{{ label.canonical_name }}</strong>
                <span class="admin-table__id">#{{ label.id }} · {{ label.normalized_name }}</span>
                <small v-if="label.description">{{ label.description }}</small>
              </td>
              <td>{{ dimensionLabel(label.dimension) }}</td>
              <td>
                <div v-if="label.aliases.length" class="admin-inline-list">
                  <span v-for="alias in label.aliases" :key="alias.id">{{ alias.alias }}</span>
                </div>
                <span v-else>无别名</span>
              </td>
              <td>
                <span :class="labelStatusClass(label.status)">
                  {{ labelStatusLabel(label.status) }}
                </span>
              </td>
              <td>{{ label.status === 'merged' ? mergedLabelName(label) : '不适用' }}</td>
              <td>{{ formatDate(label.updated_at) }}</td>
              <td>
                <div class="admin-row-actions">
                  <button class="text-button" type="button" @click="openEdit(label)">编辑</button>
                  <button class="text-button" type="button" @click="viewLabelAssignments(label)">
                    查看关联
                  </button>
                  <button
                    v-if="label.status === 'active'"
                    class="text-button"
                    type="button"
                    :disabled="rowActionId === label.id"
                    @click="openMerge(label)"
                  >
                    合并
                  </button>
                  <button
                    v-if="label.status === 'active'"
                    class="text-button text-button--danger"
                    type="button"
                    :disabled="rowActionId === label.id"
                    @click="deprecateLabel(label)"
                  >
                    废弃
                  </button>
                  <button
                    v-else-if="label.status === 'deprecated'"
                    class="text-button"
                    type="button"
                    :disabled="rowActionId === label.id"
                    @click="reactivateLabel(label)"
                  >
                    重新启用
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <nav v-if="labelMeta.total_pages > 1" class="pagination" aria-label="领域标签分页">
        <button
          class="pagination__button"
          type="button"
          :disabled="labelPage <= 1 || labelsLoading"
          @click="goToLabelPage(labelPage - 1)"
        >
          上一页
        </button>
        <span>第 {{ labelMeta.page }} / {{ labelMeta.total_pages }} 页</span>
        <button
          class="pagination__button"
          type="button"
          :disabled="labelPage >= labelMeta.total_pages || labelsLoading"
          @click="goToLabelPage(labelPage + 1)"
        >
          下一页
        </button>
      </nav>
    </template>

    <template v-else>
      <form class="admin-toolbar admin-toolbar--domain" @submit.prevent="applyAssignmentFilters">
        <label class="admin-search">
          <span class="sr-only">查找标签</span>
          <input v-model="assignmentLabelQuery" type="search" placeholder="查找标签名称或别名" />
        </label>
        <label>
          <span>选择标签</span>
          <select
            v-model.number="assignmentLabelId"
            :disabled="labelOptionsLoading"
            @change="changeAssignmentLabel"
          >
            <option value="">请选择标签</option>
            <option v-for="item in labelOptions" :key="item.id" :value="item.id">
              {{ item.canonical_name }} · {{ dimensionLabel(item.dimension) }}
            </option>
          </select>
        </label>
        <label>
          <span>审核状态</span>
          <select v-model="assignmentFilters.reviewStatus">
            <option value="">全部状态</option>
            <option v-for="item in reviewStatusOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <label>
          <span>来源</span>
          <select v-model="assignmentFilters.generationMethod">
            <option value="">全部来源</option>
            <option v-for="item in generationOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
        </label>
        <div class="admin-toolbar__actions">
          <button
            class="button button--secondary"
            type="button"
            :disabled="labelOptionsLoading"
            @click="searchAssignmentLabels"
          >
            查找
          </button>
          <button class="button button--secondary" type="button" @click="clearAssignmentFilters">
            清除
          </button>
          <button class="button button--primary" type="submit">筛选</button>
        </div>
      </form>

      <p v-if="assignmentsError" class="form-error" role="alert">{{ assignmentsError }}</p>

      <div class="admin-table-meta">
        <p>
          <template v-if="selectedAssignmentLabel">
            “{{ selectedAssignmentLabel.canonical_name }}”共 {{ assignmentMeta.total }} 条关联
          </template>
          <template v-else>请选择一个领域标签查看关联。</template>
        </p>
      </div>

      <div class="admin-table-wrap">
        <table class="admin-table admin-table--assignments">
          <thead>
            <tr>
              <th scope="col">作品版本</th>
              <th scope="col">标签</th>
              <th scope="col">来源</th>
              <th scope="col">审核状态</th>
              <th scope="col">证据</th>
              <th scope="col">模型与元数据</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="assignmentsLoading">
              <td class="admin-table__empty" colspan="7">正在读取标签关联...</td>
            </tr>
            <tr v-else-if="assignmentsError">
              <td class="admin-table__empty" colspan="7">
                <strong>{{ assignmentsError }}</strong>
                <button class="text-button" type="button" @click="loadAssignments">重试</button>
              </td>
            </tr>
            <tr v-else-if="assignmentLabelId === ''">
              <td class="admin-table__empty" colspan="7">请先选择一个领域标签。</td>
            </tr>
            <tr v-else-if="!assignments.length">
              <td class="admin-table__empty" colspan="7">该标签还没有符合条件的作品关联。</td>
            </tr>
            <tr v-for="item in assignments" v-else :key="item.id">
              <td>
                <strong>作品 #{{ item.poem_id }}</strong>
                <small>版本 v{{ item.version_no }} · 关联 #{{ item.id }}</small>
              </td>
              <td>
                <strong>{{ item.label.canonical_name }}</strong>
                <small>{{ dimensionLabel(item.label.dimension) }}</small>
              </td>
              <td>
                <span>{{ generationMethodLabel(item.generation_method) }}</span>
                <small>{{ item.origin_ref }}</small>
                <small>置信度 {{ formatConfidence(item.confidence) }}</small>
              </td>
              <td>
                <span :class="reviewStatusClass(item.review_status)">
                  {{ reviewStatusLabel(item.review_status) }}
                </span>
                <small v-if="item.reviewed_at">
                  审核于 {{ formatDate(item.reviewed_at) }}
                </small>
              </td>
              <td class="admin-table__clamp">
                <span>{{ item.evidence_text || '未提供原文证据' }}</span>
                <small>{{ formatLineRange(item) }}</small>
              </td>
              <td>
                <div class="admin-assignment-meta">
                  <strong>{{ item.model_name || '未记录模型' }}</strong>
                  <span>{{ item.task_version || '未记录任务版本' }}</span>
                  <span>创建于 {{ formatDate(item.created_at) }}</span>
                </div>
              </td>
              <td>
                <div class="admin-row-actions">
                  <button
                    v-for="action in reviewActions(item)"
                    :key="action.action"
                    class="text-button"
                    :class="{ 'text-button--danger': action.danger }"
                    type="button"
                    :disabled="rowActionId === item.id"
                    @click="reviewAssignment(item, action.action)"
                  >
                    {{ action.label }}
                  </button>
                  <span v-if="!reviewActions(item).length">无可用操作</span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <nav
        v-if="assignmentMeta.total_pages > 1"
        class="pagination"
        aria-label="标签关联分页"
      >
        <button
          class="pagination__button"
          type="button"
          :disabled="assignmentPage <= 1 || assignmentsLoading"
          @click="goToAssignmentPage(assignmentPage - 1)"
        >
          上一页
        </button>
        <span>第 {{ assignmentMeta.page }} / {{ assignmentMeta.total_pages }} 页</span>
        <button
          class="pagination__button"
          type="button"
          :disabled="assignmentPage >= assignmentMeta.total_pages || assignmentsLoading"
          @click="goToAssignmentPage(assignmentPage + 1)"
        >
          下一页
        </button>
      </nav>
    </template>

    <div v-if="editorOpen" class="admin-dialog-backdrop" @click.self="closeEditor">
      <section class="admin-dialog" role="dialog" aria-modal="true" :aria-label="editorTitle">
        <header class="admin-dialog__header">
          <div>
            <p class="section-kicker">规范标签</p>
            <h2>{{ editorTitle }}</h2>
          </div>
          <button class="admin-icon-button" type="button" aria-label="关闭" @click="closeEditor">
            ×
          </button>
        </header>

        <form class="admin-form" @submit.prevent="saveLabel">
          <label>
            <span>维度</span>
            <select v-model="editorForm.dimension" :disabled="editingLabelId !== null">
              <option v-for="item in dimensionOptions" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
          </label>
          <label>
            <span>标签名称</span>
            <input v-model="editorForm.canonicalName" type="text" maxlength="80" required />
          </label>
          <label class="admin-form__span-2">
            <span>别名</span>
            <input
              v-model="editorForm.aliasesText"
              type="text"
              placeholder="多个别名用逗号或换行分隔"
            />
          </label>
          <label class="admin-form__span-2">
            <span>说明</span>
            <textarea v-model="editorForm.description" rows="4" maxlength="500"></textarea>
          </label>

          <p v-if="editorError" class="form-error admin-form__span-2" role="alert">
            {{ editorError }}
          </p>
          <div class="admin-dialog__actions admin-form__span-2">
            <button class="button button--secondary" type="button" @click="closeEditor">
              取消
            </button>
            <button class="button button--primary" type="submit" :disabled="editorSaving">
              {{ editorSaving ? '保存中...' : '保存' }}
            </button>
          </div>
        </form>
      </section>
    </div>

    <div v-if="mergeOpen" class="admin-dialog-backdrop" @click.self="closeMerge">
      <section class="admin-dialog" role="dialog" aria-modal="true" aria-label="合并领域标签">
        <header class="admin-dialog__header">
          <div>
            <p class="section-kicker">标签归一</p>
            <h2>合并领域标签</h2>
          </div>
          <button class="admin-icon-button" type="button" aria-label="关闭" @click="closeMerge">
            ×
          </button>
        </header>

        <form class="admin-form" @submit.prevent="saveMerge">
          <div class="admin-form__span-2 admin-merge-summary">
            <span>来源标签</span>
            <strong>{{ mergeSource?.canonical_name }}</strong>
            <small>{{ mergeSource ? dimensionLabel(mergeSource.dimension) : '' }}</small>
          </div>
          <label class="admin-form__span-2">
            <span>合并到</span>
            <select v-model.number="mergeTargetId" :disabled="mergeLoading">
              <option value="">请选择同维度的启用标签</option>
              <option v-for="item in mergeTargetOptions" :key="item.id" :value="item.id">
                {{ item.canonical_name }}
              </option>
            </select>
          </label>
          <p v-if="mergeLoading" class="admin-form__loading">正在读取合并目标...</p>
          <p v-if="mergeError" class="form-error admin-form__span-2" role="alert">
            {{ mergeError }}
          </p>
          <div class="admin-dialog__actions admin-form__span-2">
            <button class="button button--secondary" type="button" @click="closeMerge">
              取消
            </button>
            <button
              class="button button--primary"
              type="submit"
              :disabled="mergeLoading || mergeSaving || !mergeTargetOptions.length"
            >
              {{ mergeSaving ? '合并中...' : '确认合并' }}
            </button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>
