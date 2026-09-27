<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  adminApi,
  type DomainLabelAssignmentPayload,
} from '@/api/admin'
import { getErrorMessage } from '@/api/http'
import type {
  DomainLabel,
  DomainLabelAssignment,
  DomainLabelDimension,
  DomainLabelGenerationMethod,
  DomainLabelReviewAction,
  DomainLabelReviewStatus,
} from '@/types/api'

const props = defineProps<{
  poemId: number
  versionNo: number
}>()

interface ReviewActionItem {
  action: DomainLabelReviewAction
  label: string
  danger?: boolean
}

const dimensionOptions: Array<{ value: DomainLabelDimension; label: string }> = [
  { value: 'imagery', label: '意象' },
  { value: 'emotion', label: '情感' },
  { value: 'theme', label: '题材' },
  { value: 'allusion', label: '典故' },
]

const generationOptions: Array<{ value: DomainLabelGenerationMethod; label: string }> = [
  { value: 'manual', label: '人工标注' },
  { value: 'public_dataset', label: '公开数据集' },
  { value: 'ai', label: 'AI 生成' },
]

const reviewStatusOptions: Array<{ value: DomainLabelReviewStatus; label: string }> = [
  { value: 'pending', label: '待审核' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已驳回' },
  { value: 'archived', label: '已归档' },
]

const assignments = ref<DomainLabelAssignment[]>([])
const activeLabels = ref<DomainLabel[]>([])
const loading = ref(true)
const labelsLoading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const createError = ref('')
const createOpen = ref(false)
const rowActionId = ref<number | null>(null)

const createForm = reactive({
  labelId: '' as number | '',
  generationMethod: 'manual' as DomainLabelGenerationMethod,
  originRef: '',
  confidence: '' as number | '',
  evidenceText: '',
  lineStart: '' as number | '',
  lineEnd: '' as number | '',
  modelName: '',
  taskVersion: '',
})

const hasAssignments = computed(() => assignments.value.length > 0)

function dimensionLabel(value: DomainLabelDimension): string {
  return dimensionOptions.find((item) => item.value === value)?.label ?? value
}

function generationMethodLabel(value: DomainLabelGenerationMethod): string {
  return generationOptions.find((item) => item.value === value)?.label ?? value
}

function reviewStatusLabel(value: DomainLabelReviewStatus): string {
  return reviewStatusOptions.find((item) => item.value === value)?.label ?? value
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

function preventPanelSubmit(event: KeyboardEvent): void {
  const target = event.target
  if (
    event.key === 'Enter' &&
    target instanceof HTMLElement &&
    (target.tagName === 'INPUT' || target.tagName === 'SELECT')
  ) {
    event.preventDefault()
  }
}

function resetCreateForm(): void {
  createForm.labelId = ''
  createForm.generationMethod = 'manual'
  createForm.originRef = ''
  createForm.confidence = ''
  createForm.evidenceText = ''
  createForm.lineStart = ''
  createForm.lineEnd = ''
  createForm.modelName = ''
  createForm.taskVersion = ''
  createError.value = ''
}

function toggleCreate(): void {
  if (createOpen.value) {
    createOpen.value = false
    resetCreateForm()
    return
  }
  resetCreateForm()
  createForm.labelId = activeLabels.value[0]?.id ?? ''
  createOpen.value = true
}

async function loadAssignments(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await adminApi.listPoemDomainLabelAssignments(props.poemId, {
      page: 1,
      page_size: 100,
    })
    assignments.value = result.items
  } catch (error) {
    assignments.value = []
    errorMessage.value = getErrorMessage(error)
  } finally {
    loading.value = false
  }
}

async function loadLabelOptions(): Promise<void> {
  labelsLoading.value = true
  try {
    const result = await adminApi.listDomainLabels({
      page: 1,
      page_size: 100,
      status: 'active',
    })
    activeLabels.value = result.items
    if (createForm.labelId === '' && result.items.length) {
      createForm.labelId = result.items[0]?.id ?? ''
    }
  } catch (error) {
    createError.value = getErrorMessage(error)
  } finally {
    labelsLoading.value = false
  }
}

async function refresh(): Promise<void> {
  await Promise.all([loadAssignments(), loadLabelOptions()])
}

async function createAssignment(): Promise<void> {
  createError.value = ''
  if (createForm.labelId === '') {
    createError.value = '请选择领域标签。'
    return
  }
  if (
    createForm.generationMethod === 'public_dataset' &&
    !createForm.originRef.trim()
  ) {
    createError.value = '公开数据集标签必须填写来源键。'
    return
  }
  if (
    createForm.generationMethod === 'ai' &&
    (!createForm.originRef.trim() ||
      !createForm.modelName.trim() ||
      !createForm.taskVersion.trim())
  ) {
    createError.value = 'AI 标签必须填写来源键、模型名和任务版本。'
    return
  }
  if (
    createForm.lineStart !== '' &&
    createForm.lineEnd !== '' &&
    createForm.lineEnd < createForm.lineStart
  ) {
    createError.value = '结束行不能小于起始行。'
    return
  }

  const payload: DomainLabelAssignmentPayload = {
    domain_label_id: createForm.labelId,
    generation_method: createForm.generationMethod,
    origin_ref: createForm.originRef.trim() || null,
    confidence: createForm.confidence === '' ? null : createForm.confidence,
    evidence_text: createForm.evidenceText.trim() || null,
    line_start: createForm.lineStart === '' ? null : createForm.lineStart,
    line_end: createForm.lineEnd === '' ? null : createForm.lineEnd,
    model_name: createForm.modelName.trim() || null,
    task_version: createForm.taskVersion.trim() || null,
  }

  saving.value = true
  try {
    await adminApi.createPoemDomainLabelAssignment(props.poemId, payload)
    ElMessage.success('领域标签已加入待审核列表')
    createOpen.value = false
    resetCreateForm()
    await loadAssignments()
  } catch (error) {
    createError.value = getErrorMessage(error)
  } finally {
    saving.value = false
  }
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
        `确认${actionLabels[action]}“${item.label.canonical_name}”关联？`,
        `${actionLabels[action]}领域标签`,
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

watch(
  () => [props.poemId, props.versionNo],
  () => {
    createOpen.value = false
    resetCreateForm()
    void refresh()
  },
  { immediate: true },
)
</script>

<template>
  <section class="poem-domain-panel" @keydown="preventPanelSubmit">
    <header class="poem-domain-panel__header">
      <div>
        <h3>领域标签</h3>
        <p>当前版本 v{{ versionNo }}，只有审核通过的标签会进入公开读取。</p>
      </div>
      <button class="button button--secondary" type="button" @click="toggleCreate">
        {{ createOpen ? '取消新增' : '新增关联' }}
      </button>
    </header>

    <div v-if="createOpen" class="poem-domain-panel__create">
      <label>
        <span>标签</span>
        <select v-model.number="createForm.labelId" :disabled="labelsLoading">
          <option value="">请选择一个启用标签</option>
          <option v-for="label in activeLabels" :key="label.id" :value="label.id">
            {{ label.canonical_name }} · {{ dimensionLabel(label.dimension) }}
          </option>
        </select>
      </label>
      <label>
        <span>来源</span>
        <select v-model="createForm.generationMethod">
          <option v-for="item in generationOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </option>
        </select>
      </label>
      <label>
        <span>起始行</span>
        <input v-model.number="createForm.lineStart" type="number" min="0" />
      </label>
      <label>
        <span>结束行</span>
        <input v-model.number="createForm.lineEnd" type="number" min="0" />
      </label>
      <label v-if="createForm.generationMethod !== 'manual'">
        <span>来源键</span>
        <input
          v-model="createForm.originRef"
          type="text"
          maxlength="120"
          placeholder="例如 public_dataset:source-v1"
        />
      </label>
      <label>
        <span>置信度</span>
        <input
          v-model.number="createForm.confidence"
          type="number"
          min="0"
          max="1"
          step="0.01"
          placeholder="0 到 1"
        />
      </label>
      <template v-if="createForm.generationMethod === 'ai'">
        <label>
          <span>模型名</span>
          <input v-model="createForm.modelName" type="text" maxlength="150" />
        </label>
        <label>
          <span>任务版本</span>
          <input v-model="createForm.taskVersion" type="text" maxlength="100" />
        </label>
      </template>
      <label class="poem-domain-panel__evidence">
        <span>原文证据</span>
        <textarea
          v-model="createForm.evidenceText"
          rows="3"
          maxlength="20000"
          placeholder="填写支持该标签的诗句或片段"
        ></textarea>
      </label>
      <p v-if="createError" class="form-error poem-domain-panel__create-error" role="alert">
        {{ createError }}
      </p>
      <div class="poem-domain-panel__create-actions">
        <button
          class="button button--primary"
          type="button"
          :disabled="saving || labelsLoading || !activeLabels.length"
          @click="createAssignment"
        >
          {{ saving ? '提交中...' : '提交待审核' }}
        </button>
      </div>
    </div>

    <div v-if="loading" class="poem-domain-panel__state">正在读取版本标签...</div>
    <div v-else-if="errorMessage" class="poem-domain-panel__state">
      <strong>{{ errorMessage }}</strong>
      <button class="text-button" type="button" @click="loadAssignments">重试</button>
    </div>
    <div v-else-if="!hasAssignments" class="poem-domain-panel__state">
      当前版本还没有领域标签关联。
    </div>
    <div v-else class="poem-domain-panel__list">
      <article
        v-for="item in assignments"
        :key="item.id"
        class="poem-domain-panel__row"
      >
        <div class="poem-domain-panel__row-main">
          <div>
            <strong>{{ item.label.canonical_name }}</strong>
            <span>{{ dimensionLabel(item.label.dimension) }} · 关联 #{{ item.id }}</span>
          </div>
          <span :class="reviewStatusClass(item.review_status)">
            {{ reviewStatusLabel(item.review_status) }}
          </span>
        </div>
        <div class="poem-domain-panel__meta">
          <span>{{ generationMethodLabel(item.generation_method) }}</span>
          <span>{{ formatLineRange(item) }}</span>
          <span>{{ item.origin_ref }}</span>
        </div>
        <p>{{ item.evidence_text || '未提供原文证据' }}</p>
        <div
          v-if="item.model_name || item.task_version"
          class="poem-domain-panel__model"
        >
          <span>{{ item.model_name || '未记录模型' }}</span>
          <span>{{ item.task_version || '未记录任务版本' }}</span>
        </div>
        <div class="poem-domain-panel__actions">
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
      </article>
    </div>
  </section>
</template>

<style scoped>
.poem-domain-panel {
  padding-top: 22px;
  border-top: 1px solid var(--line);
}

.poem-domain-panel__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.poem-domain-panel__header h3 {
  margin-bottom: 5px;
  font-size: 20px;
}

.poem-domain-panel__header p {
  margin-bottom: 0;
  color: var(--ink-soft);
  font-size: 13px;
}

.poem-domain-panel__create {
  display: grid;
  margin-top: 18px;
  padding: 16px;
  border-left: 4px solid var(--jade);
  background: rgba(33, 106, 89, 0.045);
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.poem-domain-panel__create label {
  display: grid;
  color: var(--ink-soft);
  font-size: 13px;
  gap: 7px;
}

.poem-domain-panel__create input,
.poem-domain-panel__create select,
.poem-domain-panel__create textarea {
  width: 100%;
  min-height: 42px;
  padding: 9px 11px;
  color: var(--ink);
  border: 1px solid var(--line);
  border-radius: 4px;
  background: var(--white);
}

.poem-domain-panel__create textarea {
  resize: vertical;
  line-height: 1.7;
}

.poem-domain-panel__evidence,
.poem-domain-panel__create-error,
.poem-domain-panel__create-actions {
  grid-column: 1 / -1;
}

.poem-domain-panel__create-error {
  margin-bottom: 0;
}

.poem-domain-panel__create-actions {
  display: flex;
  justify-content: flex-end;
}

.poem-domain-panel__state {
  display: flex;
  min-height: 110px;
  margin-top: 16px;
  color: var(--ink-soft);
  border-top: 1px solid var(--line);
  align-items: center;
  justify-content: center;
  gap: 10px;
  text-align: center;
}

.poem-domain-panel__state strong {
  color: var(--ink);
}

.poem-domain-panel__list {
  margin-top: 16px;
  border-top: 1px solid var(--line);
}

.poem-domain-panel__row {
  padding: 15px 0;
  border-bottom: 1px solid var(--line);
}

.poem-domain-panel__row-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.poem-domain-panel__row-main strong,
.poem-domain-panel__row-main span {
  display: block;
}

.poem-domain-panel__row-main strong {
  font-family: "Noto Serif SC", "Songti SC", serif;
  font-size: 18px;
}

.poem-domain-panel__row-main div > span,
.poem-domain-panel__meta,
.poem-domain-panel__model {
  color: #718084;
  font-size: 12px;
}

.poem-domain-panel__meta,
.poem-domain-panel__model {
  display: flex;
  margin-top: 7px;
  flex-wrap: wrap;
  gap: 6px 14px;
}

.poem-domain-panel__meta span + span::before,
.poem-domain-panel__model span + span::before {
  margin-right: 14px;
  content: "·";
}

.poem-domain-panel__row p {
  margin: 8px 0 0;
  color: var(--ink-soft);
  font-size: 14px;
  line-height: 1.7;
}

.poem-domain-panel__actions {
  display: flex;
  margin-top: 9px;
  color: #718084;
  font-size: 13px;
  gap: 14px;
}

@media (max-width: 680px) {
  .poem-domain-panel__header {
    align-items: stretch;
    flex-direction: column;
  }

  .poem-domain-panel__create {
    grid-template-columns: 1fr;
  }

  .poem-domain-panel__evidence,
  .poem-domain-panel__create-error,
  .poem-domain-panel__create-actions {
    grid-column: auto;
  }
}
</style>
