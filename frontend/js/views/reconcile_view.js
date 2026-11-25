export const reconcileViewTemplate = `
  <div class="grid-2">
    <div class="panel">
      <h3>许可对账（合格灯与封批快照同代）</h3>
      <div class="actions"><button class="secondary" @click="loadReconcile">刷新全部</button></div>
      <table data-reconcile-table>
        <thead><tr><th>许可</th><th>灯</th><th>灯代</th><th>证明</th><th>封批数</th><th>漂移</th></tr></thead>
        <tbody>
          <tr v-for="r in reconAllItems" :key="r.permit_code" :data-permit="r.permit_code">
            <td>{{ r.permit_code }}</td>
            <td :class="r.balanced?'pass':'fail'" data-lamp>
              {{ r.balanced ? "● 合格" : "● 不合格" }}
            </td>
            <td data-lamp-generation>G{{ r.lamp_generation || "—" }}</td>
            <td :class="r.proof_ready?'pass':'warn'">{{ r.proof_ready ? "可证" : "未齐" }}</td>
            <td>{{ r.seal_count || 0 }}</td>
            <td :class="r.drift_count ? 'fail' : 'muted'">
              {{ r.drift_count ? (r.drift_count + " 处封后漂移") : "无" }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>单许可 / 采样证明</h3>
      <label>许可号</label><input v-model="reconCode"/>
      <div class="actions"><button @click="loadReconcileOne">对账</button></div>

      <table v-if="reconOne" style="margin-top:0.6rem" data-recon-samples>
        <thead><tr><th>采样</th><th>判定灯</th><th>同代</th><th>判定源</th><th>漂移</th></tr></thead>
        <tbody>
          <tr v-for="s in reconOne.samples" :key="s.sample_code" :data-recon-sample="s.sample_code">
            <td>{{ s.sample_code }}</td>
            <td :class="s.matched ? 'pass' : 'fail'">{{ s.matched ? "合格" : "不合格" }}</td>
            <td>G{{ s.lamp_generation }}</td>
            <td class="muted">{{ s.judged_source === "seal_snapshot" ? "封批快照" : "正式列(未封批)" }}</td>
            <td :class="s.metrics_drift ? 'fail' : 'muted'">{{ s.metrics_drift ? "封后禁改偏航" : "—" }}</td>
          </tr>
        </tbody>
      </table>

      <pre v-if="reconOne" style="white-space:pre-wrap;font-size:0.82rem">{{ JSON.stringify(reconOne, null, 2) }}</pre>
      <label>采样号证明</label><input v-model="proofCode"/>
      <div class="actions"><button class="secondary" @click="loadProof">证明</button></div>
      <pre v-if="proofOne" style="white-space:pre-wrap;font-size:0.82rem">{{ JSON.stringify(proofOne, null, 2) }}</pre>
    </div>
  </div>`;
