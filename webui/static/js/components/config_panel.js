// 配置面板：由 /api/webui/config/schema 自动生成表单
// Config panel: form auto-generated from the schema API.
// 核心项直接可见；高级项收进每组的 <details> 折叠区。
// 运行中整体禁用（服务端 409 双保险）。

import { html, useState, useEffect } from "/static/vendor/preact-htm-standalone.mjs";
import { api } from "../api.js";

function FieldInput({ item, value, onChange, disabled }) {
  if (item.sensitive) {
    return html`<span class="badge ${item.configured ? "ok" : "err"}">
      ${item.configured ? "已配置 ✓" : "未配置 ✗"}
    </span>`;
  }
  const lock = disabled || item.restart_required;
  if (item.type === "bool") {
    return html`<input type="checkbox" checked=${!!value} disabled=${lock}
      onChange=${(e) => onChange(e.target.checked)} />`;
  }
  if (item.type === "enum") {
    return html`<select value=${value ?? ""} disabled=${lock}
      onChange=${(e) => onChange(e.target.value)}>
      ${item.options.map((o) => html`<option value=${o}>${o}</option>`)}
    </select>`;
  }
  if (item.type === "int" || item.type === "float") {
    return html`<input type="number" step=${item.type === "float" ? "any" : "1"}
      value=${value ?? ""} disabled=${lock}
      onChange=${(e) => onChange(e.target.value)} />`;
  }
  return html`<input type="text" value=${value ?? ""} disabled=${lock}
    onChange=${(e) => onChange(e.target.value)} />`;
}

function Field({ item, value, dirty, onChange, disabled }) {
  return html`
    <div class="cfg-field ${dirty ? "dirty" : ""}" title=${item.description || item.name}>
      <label class="cfg-label">
        <span class="cfg-name">${item.label}</span>
        <code class="cfg-env">${item.name}</code>
        ${item.restart_required && html`<span class="cfg-note">需重启</span>`}
        ${item.derived_note && html`<span class="cfg-note" title=${item.derived_note}>派生</span>`}
      </label>
      <div class="cfg-input">
        <${FieldInput} item=${item} value=${value} onChange=${onChange} disabled=${disabled} />
      </div>
    </div>
  `;
}

export function ConfigPanel({ running, onApply, sessionOverrides }) {
  const [schema, setSchema] = useState(null);
  const [values, setValues] = useState({});     // 服务端当前生效值 / live values
  const [edits, setEdits] = useState({});       // 用户改动 / user edits
  const [error, setError] = useState(null);
  const [fieldErrors, setFieldErrors] = useState({});

  const load = async () => {
    try {
      const [s, v] = await Promise.all([api.getConfigSchema(), api.getConfigValues()]);
      setSchema(s);
      // /config/values contains repository defaults.  A WebUI session keeps
      // its own explicit overrides, so display the effective session values
      // instead of snapping back to defaults after every session rebuild.
      setValues({ ...v, ...(sessionOverrides || {}) });
      setEdits({});
      setFieldErrors({});
      setError(null);
    } catch (e) {
      setError(`加载配置失败: ${e.message}`);
    }
  };

  useEffect(() => { load(); }, []);
  // 会话变化（新建/关闭）后刷新生效值 / refresh after session changes
  useEffect(() => { if (schema) load(); }, [JSON.stringify(sessionOverrides || {})]);

  if (error) return html`<div class="placeholder">${error}</div>`;
  if (!schema) return html`<div class="placeholder">加载配置…</div>`;

  const dirtyCount = Object.keys(edits).length;

  const setEdit = (item, raw) => {
    // 数字输入框给的是字符串；与生效值一致时撤销改动
    // number inputs give strings; drop the edit when equal to live value
    const live = values[item.name];
    let v = raw;
    if (item.type === "int") v = raw === "" ? "" : parseInt(raw, 10);
    if (item.type === "float") v = raw === "" ? "" : parseFloat(raw);
    setEdits((prev) => {
      const next = { ...prev };
      if (v === live || v === "" || Number.isNaN(v)) delete next[item.name];
      else next[item.name] = v;
      return next;
    });
  };

  const applyAndNewSession = async () => {
    setFieldErrors({});
    setError(null);
    try {
      // A session is replaced as a whole. Preserve its previous choices when
      // the user changes only one field in a later apply operation.
      const nextOverrides = { ...(sessionOverrides || {}), ...edits };
      await onApply(nextOverrides);
      // The parent session update will trigger load() with the new props. Keep
      // this render coherent in the meantime instead of racing an old-props
      // load against that effect.
      setValues((prev) => ({ ...prev, ...nextOverrides }));
      setEdits({});
    } catch (e) {
      if (e.status === 422 && e.data && e.data.errors) setFieldErrors(e.data.errors);
      else setError(e.message);
    }
  };

  return html`
    <div class="config-panel">
      <div class="cfg-head">
        <span class="cfg-title">配置</span>
        <span class="spacer"></span>
        <button onClick=${load} disabled=${running}>重置</button>
        <button class="primary" onClick=${applyAndNewSession} disabled=${running}>
          应用并新建会话${dirtyCount ? ` (${dirtyCount})` : ""}
        </button>
      </div>
      ${running && html`<div class="cfg-locked">任务运行中，配置已锁定</div>`}
      ${Object.entries(fieldErrors).map(
        ([k, msg]) => html`<div class="cfg-error">${k}: ${msg}</div>`
      )}
      ${schema.groups.map((g) => {
        const core = g.items.filter((it) => it.core);
        const adv = g.items.filter((it) => !it.core);
        const render = (it) => html`
          <${Field} key=${it.name} item=${it}
            value=${it.name in edits ? edits[it.name] : values[it.name]}
            dirty=${it.name in edits}
            disabled=${running}
            onChange=${(v) => setEdit(it, v)} />
        `;
        return html`
          <section class="cfg-group" key=${g.id}>
            <h3 class="cfg-group-title">${g.title}</h3>
            ${core.map(render)}
            ${adv.length > 0 && html`
              <details class="cfg-advanced">
                <summary>高级 (${adv.length})</summary>
                ${adv.map(render)}
              </details>
            `}
          </section>
        `;
      })}
    </div>
  `;
}
