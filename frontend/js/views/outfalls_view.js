export const outfallsViewTemplate = `
  <div class="grid-2">
    <div class="panel">
      <h3>排放口限值</h3>
      <table>
        <thead><tr><th>编码</th><th>厂</th><th>河段</th><th>COD</th><th>氨氮</th><th>总磷</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="o in outfalls" :key="o.id">
            <td>{{ o.outfall_code }}</td><td>{{ o.plant_code }}</td><td>{{ o.river_reach }}</td>
            <td>{{ o.cod_limit }}</td><td>{{ o.nh3n_limit }}</td><td>{{ o.tp_limit }}</td>
            <td :class="statusClass(o.status)">{{ o.status }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>新建排放口</h3>
      <div class="form-row"><div><label>编码</label><input v-model="outfallForm.outfall_code"/></div>
      <div><label>处理厂 ID</label><input type="number" v-model.number="outfallForm.plant_id"/></div></div>
      <label>河段</label><input v-model="outfallForm.river_reach"/>
      <div class="form-row">
        <div><label>COD 限</label><input type="number" v-model.number="outfallForm.cod_limit"/></div>
        <div><label>氨氮限</label><input type="number" v-model.number="outfallForm.nh3n_limit"/></div>
      </div>
      <label>总磷限</label><input type="number" v-model.number="outfallForm.tp_limit"/>
      <div class="actions"><button @click="createOutfall">登记</button></div>
    </div>
  </div>

  <div class="panel" style="margin-top:0.9rem" data-curve-panel>
    <h3>排放口曲线 · 采样点与正式指标同戳</h3>
    <label>选择排放口</label>
    <select v-model.number="curveOutfallId" @change="loadCurve(curveOutfallId)" data-curve-outfall>
      <option v-for="o in outfalls" :key="o.id" :value="o.id">{{ o.outfall_code }} · {{ o.river_reach }}</option>
    </select>
    <p v-if="!curvePoints.length" class="muted">该排放口暂无正式指标点。</p>

    <div v-for="key in ['cod','nh3n','tp']" :key="key" class="curve-chart" data-curve-chart :data-metric="key">
      <h4>{{ metricLabel(key) }} 曲线</h4>
      <svg width="600" height="150" viewBox="0 0 600 150" class="curve-svg" role="img">
        <polyline :points="curveLine(key)" fill="none" stroke="#2f6f6a" stroke-width="2"/>
        <circle v-for="(p,i) in curvePoints" :key="p.sample_id + '-' + key"
                :cx="curveX(i)" :cy="curveY(p[key], key)" r="4.5"
                fill="#c2604f" data-curve-point :data-ts="p.ts" :data-metric="key"
                :data-sample="p.sample_code">
          <title>{{ p.sample_code }} {{ metricLabel(key) }}={{ p[key] }} 同戳 ts={{ p.ts }}</title>
        </circle>
      </svg>
    </div>

    <table v-if="curvePoints.length" data-curve-table style="margin-top:0.6rem">
      <thead><tr><th>采样</th><th>点戳（=正式指标行时间戳）</th><th>COD</th><th>氨氮</th><th>总磷</th><th>代</th><th>封批代</th></tr></thead>
      <tbody>
        <tr v-for="p in curvePoints" :key="'row-'+p.sample_id" data-curve-row>
          <td>{{ p.sample_code }}</td>
          <td data-curve-ts :data-ts="p.ts">{{ p.ts }}</td>
          <td>{{ p.cod }}</td><td>{{ p.nh3n }}</td><td>{{ p.tp }}</td>
          <td>{{ p.generation }}</td>
          <td>{{ p.seal_generation ? ("G" + p.seal_generation) : "—" }}</td>
        </tr>
      </tbody>
    </table>
  </div>`;
