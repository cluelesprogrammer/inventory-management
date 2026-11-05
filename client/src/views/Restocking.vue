<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <!-- Budget control stays mounted during refetches so dragging the slider is never interrupted -->
    <div class="card">
      <div class="card-header">
        <h3 class="card-title">{{ t('restocking.budget') }}</h3>
        <span class="budget-readout">{{ formatCurrency(budget, currentCurrency) }}</span>
      </div>
      <div class="budget-control">
        <input
          v-model.number="budget"
          type="range"
          class="budget-slider"
          min="0"
          max="100000"
          step="500"
          :aria-label="t('restocking.budgetSlider')"
        />
        <div class="budget-scale">
          <span>{{ formatCurrency(0, currentCurrency) }}</span>
          <span>{{ formatCurrency(100000, currentCurrency) }}</span>
        </div>
      </div>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.itemCount') }}</div>
          <div class="stat-value">{{ items.length }}</div>
        </div>
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.totalCost') }}</div>
          <div class="stat-value">{{ formatCurrency(totalCost, currentCurrency) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.remaining') }}</div>
          <div class="stat-value">{{ formatCurrency(remaining, currentCurrency) }}</div>
        </div>
      </div>

      <div v-if="orderSuccess" class="notice success" role="status">{{ orderSuccess }}</div>
      <div v-if="orderError" class="error" role="alert">{{ orderError }}</div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendations') }} ({{ items.length }})</h3>
          <button
            type="button"
            class="place-order-btn"
            :disabled="!canPlaceOrder"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="items.length === 0" class="empty-state">{{ t('restocking.noRecommendations') }}</div>
        <div v-else class="table-container">
          <table class="restocking-table">
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.name') }}</th>
                <th class="num">{{ t('restocking.table.quantity') }}</th>
                <th class="num">{{ t('restocking.table.unitCost') }}</th>
                <th class="num">{{ t('restocking.table.lineTotal') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="item.sku">
                <td><strong>{{ item.sku }}</strong></td>
                <td>{{ translateProductName(item.name) }}</td>
                <td class="num">{{ item.quantity.toLocaleString() }}</td>
                <td class="num">{{ formatCurrencyWithDecimals(item.unit_cost, currentCurrency, 2) }}</td>
                <td class="num"><strong>{{ formatCurrency(item.line_total, currentCurrency) }}</strong></td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td colspan="4" class="foot-label">{{ t('restocking.totalCost') }}</td>
                <td class="num"><strong>{{ formatCurrency(totalCost, currentCurrency) }}</strong></td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

const { t, currentCurrency, currentLocale, translateProductName } = useI18n()

const DEBOUNCE_MS = 300

const budget = ref(25000)
const recommendation = ref({ budget: 0, total_cost: 0, remaining: 0, items: [] })
const loading = ref(true) // initial load only; slider refetches keep the table visible
const error = ref(null)
const pending = ref(false) // slider moved but the matching recommendation has not arrived yet
const submitting = ref(false)
const orderSuccess = ref('')
const orderError = ref('')

const items = computed(() => recommendation.value.items || [])
const totalCost = computed(() => recommendation.value.total_cost || 0)
const remaining = computed(() => recommendation.value.remaining || 0)
const canPlaceOrder = computed(() => items.value.length > 0 && !submitting.value && !pending.value)

// Safe date formatting: invalid/missing dates render as an empty placeholder instead of "Invalid Date"
const formatDate = (dateString) => {
  const date = new Date(dateString)
  if (!dateString || isNaN(date.getTime())) return '-'
  const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
  return date.toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' })
}

// Request counter so a slow earlier response can never overwrite a newer one
let requestId = 0

const loadRecommendations = async () => {
  const myId = ++requestId
  try {
    error.value = null
    const data = await api.getRestockingRecommendations(budget.value)
    if (myId !== requestId) return
    recommendation.value = data
  } catch (err) {
    if (myId !== requestId) return
    error.value = t('restocking.loadFailed') + ': ' + (err.response?.data?.detail || err.message)
  } finally {
    if (myId === requestId) {
      loading.value = false
      pending.value = false
    }
  }
}

// Debounce: wait DEBOUNCE_MS after the last slider change before hitting the API
let debounceTimer = null
watch(budget, () => {
  pending.value = true
  orderSuccess.value = ''
  orderError.value = ''
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(loadRecommendations, DEBOUNCE_MS)
})

const placeOrder = async () => {
  if (!canPlaceOrder.value) return
  submitting.value = true
  orderSuccess.value = ''
  orderError.value = ''
  try {
    // Send exactly what is displayed: the budget it was computed for plus sku/quantity only
    const created = await api.placeRestockingOrder({
      budget: recommendation.value.budget,
      items: items.value.map(({ sku, quantity }) => ({ sku, quantity }))
    })
    orderSuccess.value = t('restocking.orderPlaced', {
      orderNumber: created.order_number,
      date: formatDate(created.expected_delivery)
    })
    await loadRecommendations()
  } catch (err) {
    // Backend returns a human-readable `detail` on 400s (over budget, unknown SKU, ...)
    orderError.value = err.response?.data?.detail || t('restocking.orderFailed')
  } finally {
    submitting.value = false
  }
}

onMounted(loadRecommendations)
onBeforeUnmount(() => clearTimeout(debounceTimer))
</script>

<style scoped>
.budget-readout {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
}

.budget-control {
  padding: 0.5rem 0 0.25rem;
}

.budget-slider {
  width: 100%;
  accent-color: #2563eb;
}

.budget-scale {
  display: flex;
  justify-content: space-between;
  color: #64748b;
  font-size: 0.813rem;
}

.restocking-table {
  width: 100%;
}

.restocking-table .num {
  text-align: right;
  white-space: nowrap;
}

.foot-label {
  text-align: right;
  font-weight: 600;
  color: #64748b;
}

.place-order-btn {
  background: #0f172a;
  color: white;
  border: 1px solid #0f172a;
  border-radius: 8px;
  padding: 0.5rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease;
}

.place-order-btn:hover:not(:disabled) {
  background: #1e293b;
}

.place-order-btn:disabled {
  background: #e2e8f0;
  border-color: #e2e8f0;
  color: #64748b;
  cursor: not-allowed;
}

.notice.success {
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #065f46;
  padding: 1rem;
  border-radius: 8px;
  margin: 0 0 1.25rem;
  font-size: 0.938rem;
}

.empty-state {
  text-align: center;
  padding: 2rem 1rem;
  color: #64748b;
  font-size: 0.938rem;
}

.error {
  margin: 0 0 1.25rem;
}
</style>
