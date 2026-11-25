export const sealViewTemplate = `
  <div class="grid-2">
    <div class="panel">
      <h3>封批快照</h3>
      <p class="footnote">封批代一旦生效，采样页 COD / 氨氮 / 总磷立即只读，正式指标表零改拒写。</p>
      <table>
        <thead><tr><th>封批号</th><th>采样</th><th>代</th><th>生效标记</th><th>快照</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="s in seals" :key="s.id" :data-seal-row="s.sample_code">
            <td>{{ s.seal_ref }}</td><td>{{ s.sample_code }}</td><td>{{ s.sample_generation }}</td>
            <td>
              <span v-if="s.effective" class="badge lock" data-seal-effective-marker
                    :data-sample="s.sample_code" data-testid="seal-effective-marker">
                🔒 {{ s.effective_marker || ("封批代已生效 · G" + s.sample_generation) }}
              </span>
              <span v-else class="muted">{{ s.effective_marker || "未生效" }}</span>
            </td>
            <td>{{ s.snapshot }}</td><td>{{ s.sealed_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>执行封批</h3>
      <label>可封批采样</label>
      <select v-model.number="sealForm.sample_id">
        <option v-for="s in sealableSamples" :key="s.id" :value="s.id">{{ s.sample_code }} · {{ s.status }}</option>
      </select>
      <label>快照（可空自动）</label><input v-model="sealForm.snapshot"/>
      <div class="actions"><button class="warn" @click="createSeal">封批升代</button></div>
      <div v-if="sealEffectiveProbe" class="banner err" style="margin-top:0.8rem" data-effective-probe>
        {{ sealEffectiveProbe.sample_code }}：{{ sealEffectiveProbe.marker }}，三指标只读
      </div>
    </div>
  </div>`;
