export const lineageViewTemplate = `
  <div class="grid-2">
    <div class="panel">
      <h3>指标血缘（正式层）</h3>
      <p class="footnote">未升正的封后影子修订一律不进入本主表（effluent-04 乙项警戒）。</p>
      <table data-lineage-main>
        <thead><tr><th>从</th><th>到</th><th>边类型</th><th>说明</th><th>层</th></tr></thead>
        <tbody>
          <tr v-for="e in lineageEdges" :key="e.id" data-lineage-row>
            <td>{{ e.from_code }}</td><td>{{ e.to_code || "—" }}</td>
            <td>{{ e.edge_type }}</td><td>{{ e.detail }}</td>
            <td><span class="badge official">正式</span></td>
          </tr>
        </tbody>
      </table>
      <div class="actions">
        <button class="secondary" @click="toggleDrafts" data-toggle-drafts>
          {{ showDrafts ? "收起影子草稿隔离台账" : "展开影子草稿隔离台账（取证，不混入血缘）" }}
        </button>
      </div>
      <div v-if="showDrafts" class="panel" style="margin-top:0.6rem;border-style:dashed" data-shadow-isolated>
        <h3>影子指标草稿 · 隔离区（未升正）</h3>
        <table>
          <thead><tr><th>草稿号</th><th>采样</th><th>状态</th><th>指标</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="d in metricDrafts" :key="d.id" data-draft-row>
              <td>{{ d.draft_code }}</td><td>{{ d.sample_code }}</td>
              <td class="warn">{{ d.status }}</td>
              <td>{{ draftFmt(d.metrics) }}</td><td>{{ d.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <h3>登记血缘边</h3>
      <div class="form-row"><div><label>源采样 ID</label><input type="number" v-model.number="lineageForm.from_sample_id"/></div>
      <div><label>目标 ID</label><input type="number" v-model.number="lineageForm.to_sample_id"/></div></div>
      <label>边类型</label><input v-model="lineageForm.edge_type"/>
      <label>说明</label><input v-model="lineageForm.detail"/>
      <div class="actions"><button @click="createLineage">写入正式血缘</button></div>
      <p class="footnote">写「封后影子修订」边会被血缘服务以 shadow_lineage_forbidden 拒绝。</p>
    </div>
  </div>`;
