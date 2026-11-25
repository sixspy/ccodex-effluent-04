export const samplesViewTemplate = `
  <div class="grid-2">
    <div class="panel">
      <h3>采样批次</h3>
      <table>
        <thead><tr><th>编号</th><th>口</th><th>许可</th><th>流量</th><th>状态</th><th>代</th><th>封后只读</th></tr></thead>
        <tbody>
          <tr v-for="s in samples" :key="s.id" @click="pickSample(s)" style="cursor:pointer"
              :class="{'row-selected': metricTarget.id===s.id}" data-sample-row>
            <td>{{ s.sample_code }}</td><td>{{ s.outfall_code }}</td><td>{{ s.permit_ref }}</td>
            <td>{{ s.flow_m3h }}</td><td :class="sampleClass(s.status)">{{ s.status }}</td>
            <td>{{ s.generation }}</td>
            <td>
              <span v-if="s.metrics_readonly" class="badge lock" data-row-readonly>封批生效·只读</span>
              <span v-else class="muted">可改</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="sampleDetail" class="panel" style="margin-top:0.8rem;border-style:dashed">
        <h3>批次详情</h3>
        <div class="kv">
          <span>指标</span><span>{{ fmtMetrics(sampleDetail.sample.metrics) }}</span>
          <span>挂起</span><span>{{ sampleDetail.exceed_holds.length }}</span>
          <span>准排</span><span>{{ sampleDetail.discharge_releases.length }}</span>
          <span>封批</span><span>{{ sampleDetail.discharge_seals.length }}</span>
        </div>
        <div v-if="sampleDetail.sample.drift_note" class="banner err" style="margin-top:0.6rem" data-drift-note>
          {{ sampleDetail.sample.drift_note }}
        </div>
      </div>
    </div>
    <div>
      <div class="panel" id="metric-edit-panel" data-metric-panel>
        <h3>指标回写（封后禁改）</h3>
        <div v-if="!metricTarget.id" class="muted">点选左侧批次后回写指标。</div>
        <template v-else>
          <div class="kv" style="margin-bottom:0.5rem">
            <span>批次</span><span><strong>{{ metricTarget.sample_code }}</strong> · 代 {{ metricTarget.generation }}</span>
          </div>
          <div v-if="metricTarget.readonly" class="banner err" data-readonly-banner>
            🔒 {{ metricTarget.marker || ("封批代已生效 · G" + metricTarget.generation) }}：
            COD / 氨氮 / 总磷输入只读，正式指标表零改，强写按「封后禁改偏航」拒写并留痕。
          </div>
          <div v-else class="banner ok" style="background:#eaf4ee;color:var(--ok)">该批次封批未生效，指标可回写（仍抢批锁）。</div>
          <div class="form-row">
            <div>
              <label for="metric-cod">COD</label>
              <input id="metric-cod" data-metric-input="cod" type="number" step="0.1"
                     v-model.number="metricTarget.cod" :readonly="metricTarget.readonly"
                     :aria-readonly="metricTarget.readonly ? 'true' : 'false'"
                     :class="{'readonly-input': metricTarget.readonly}"/>
            </div>
            <div>
              <label for="metric-nh3n">氨氮</label>
              <input id="metric-nh3n" data-metric-input="nh3n" type="number" step="0.01"
                     v-model.number="metricTarget.nh3n" :readonly="metricTarget.readonly"
                     :aria-readonly="metricTarget.readonly ? 'true' : 'false'"
                     :class="{'readonly-input': metricTarget.readonly}"/>
            </div>
          </div>
          <div class="form-row">
            <div>
              <label for="metric-tp">总磷</label>
              <input id="metric-tp" data-metric-input="tp" type="number" step="0.001"
                     v-model.number="metricTarget.tp" :readonly="metricTarget.readonly"
                     :aria-readonly="metricTarget.readonly ? 'true' : 'false'"
                     :class="{'readonly-input': metricTarget.readonly}"/>
            </div>
            <div>
              <label>批锁键</label>
              <input :value="metricTarget.lock_key || ('sample:' + metricTarget.id)" readonly class="readonly-input"/>
            </div>
          </div>
          <div class="actions">
            <button id="metric-save-btn" @click="saveMetric"
                    :disabled="metricTarget.readonly"
                    :class="{'btn-disabled': metricTarget.readonly}"
                    data-metric-save>回写指标</button>
            <button class="secondary" @click="reloadMetricTarget">重读</button>
          </div>
        </template>
      </div>

      <div class="panel">
        <h3>入库采样（新批次）</h3>
        <div class="form-row"><div><label>排放口 ID</label><input type="number" v-model.number="sampleForm.outfall_id"/></div>
        <div><label>许可号</label><input v-model="sampleForm.permit_ref"/></div></div>
        <div class="form-row">
          <div><label>COD（新批次）</label><input data-create-input="cod" type="number" v-model.number="sampleForm.cod"/></div>
          <div><label>氨氮（新批次）</label><input data-create-input="nh3n" type="number" v-model.number="sampleForm.nh3n"/></div>
        </div>
        <div class="form-row">
          <div><label>总磷（新批次）</label><input data-create-input="tp" type="number" v-model.number="sampleForm.tp"/></div>
          <div><label>流量 m3/h</label><input type="number" v-model.number="sampleForm.flow_m3h"/></div>
        </div>
        <label>编号（可空自动）</label><input v-model="sampleForm.sample_code"/>
        <div class="actions"><button @click="createSample">入库</button></div>
      </div>
    </div>
  </div>`;
