const { useEffect, useMemo, useState } = React;
const html = htm.bind(React.createElement);

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      if (payload && payload.detail) {
        detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
      }
    } catch (_) {
      // Keep fallback detail.
    }
    throw new Error(detail);
  }
  return response.json();
}

function normalizeSearchText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function useDebouncedValue(value, delayMs) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

function App() {
  const [roles, setRoles] = useState([]);
  const [listStatus, setListStatus] = useState({ text: "Loading job roles...", tone: "" });
  const [selectedSector, setSelectedSector] = useState("");
  const [selectedTrack, setSelectedTrack] = useState("");
  const [roleSearch, setRoleSearch] = useState("");
  const [selectedRoleId, setSelectedRoleId] = useState(null);
  const [roleDetail, setRoleDetail] = useState(null);
  const [detailStatus, setDetailStatus] = useState({ text: "Select a job role to inspect its framework mapping.", tone: "" });

  const debouncedSearch = useDebouncedValue(roleSearch, 120);

  useEffect(() => {
    let cancelled = false;

    async function loadRoles() {
      setListStatus({ text: "Loading job roles...", tone: "" });
      try {
        const rows = await fetchJson("/api/public/job-roles?limit=5000");
        if (cancelled) {
          return;
        }
        setRoles(Array.isArray(rows) ? rows : []);
        setListStatus({
          text: Array.isArray(rows) && rows.length
            ? `${rows.length} job role(s) loaded.`
            : "No job roles were returned.",
          tone: Array.isArray(rows) && rows.length ? "success" : "warning",
        });
      } catch (error) {
        if (cancelled) {
          return;
        }
        setRoles([]);
        setListStatus({ text: `Unable to load job roles: ${error.message}`, tone: "error" });
      }
    }

    loadRoles();
    return () => {
      cancelled = true;
    };
  }, []);

  const sectorOptions = useMemo(() => {
    return Array.from(new Set(roles.map((role) => role.sector).filter(Boolean))).sort((a, b) => a.localeCompare(b));
  }, [roles]);

  const trackOptions = useMemo(() => {
    const source = selectedSector
      ? roles.filter((role) => role.sector === selectedSector)
      : roles;
    return Array.from(new Set(source.map((role) => role.track).filter(Boolean))).sort((a, b) => a.localeCompare(b));
  }, [roles, selectedSector]);

  useEffect(() => {
    if (selectedTrack && !trackOptions.includes(selectedTrack)) {
      setSelectedTrack("");
    }
  }, [selectedTrack, trackOptions]);

  const visibleRoles = useMemo(() => {
    const normalizedQuery = normalizeSearchText(debouncedSearch);
    return roles.filter((role) => {
      if (selectedSector && role.sector !== selectedSector) {
        return false;
      }
      if (selectedTrack && role.track !== selectedTrack) {
        return false;
      }
      if (!normalizedQuery) {
        return true;
      }
      const haystack = normalizeSearchText(`${role.job_role_name} ${role.track} ${role.sector}`);
      return haystack.includes(normalizedQuery);
    });
  }, [roles, selectedSector, selectedTrack, debouncedSearch]);

  useEffect(() => {
    if (!visibleRoles.length) {
      setSelectedRoleId(null);
      setRoleDetail(null);
      setDetailStatus({ text: "No job roles match the current filters.", tone: "warning" });
      return;
    }

    const roleStillVisible = visibleRoles.some((role) => role.job_role_id === selectedRoleId);
    if (!roleStillVisible) {
      setSelectedRoleId(visibleRoles[0].job_role_id);
    }
  }, [visibleRoles, selectedRoleId]);

  useEffect(() => {
    if (!selectedRoleId) {
      return;
    }

    let cancelled = false;

    async function loadRoleDetail() {
      setDetailStatus({ text: "Loading job role detail...", tone: "" });
      try {
        const detail = await fetchJson(`/api/public/job-roles/${encodeURIComponent(selectedRoleId)}`);
        if (cancelled) {
          return;
        }
        setRoleDetail(detail);
        const skillCount = Array.isArray(detail.skills) ? detail.skills.length : 0;
        const workFunctionCount = Array.isArray(detail.critical_work_functions)
          ? detail.critical_work_functions.length
          : 0;
        setDetailStatus({
          text: `${skillCount} mapped skill group(s) and ${workFunctionCount} critical work function(s).`,
          tone: "success",
        });
      } catch (error) {
        if (cancelled) {
          return;
        }
        setRoleDetail(null);
        setDetailStatus({ text: `Unable to load role detail: ${error.message}`, tone: "error" });
      }
    }

    loadRoleDetail();
    return () => {
      cancelled = true;
    };
  }, [selectedRoleId]);

  const currentRole = visibleRoles.find((role) => role.job_role_id === selectedRoleId) || null;

  const metrics = useMemo(() => {
    const sectorCount = sectorOptions.length;
    const trackCount = Array.from(new Set(roles.map((role) => `${role.sector}::${role.track}`))).length;
    return {
      roleCount: roles.length,
      sectorCount,
      trackCount,
    };
  }, [roles, sectorOptions]);

  const clearFilters = () => {
    setSelectedSector("");
    setSelectedTrack("");
    setRoleSearch("");
  };

  return html`
    <main className="academy-shell role-lookup-shell">
      <header className="hero">
        <div className="hero-brand">
          <img
            className="hero-logo"
            src="/academy/static/Blue Elephants Logo.png"
            alt="Blue Elephants Solutions logo"
          />
        </div>
        <p className="eyebrow">SkillsFuture Side Feature</p>
        <h1>Job Role Lookup</h1>
        <p className="hero-copy">
          Read-only lookup for SkillsFuture job roles, critical work functions, key tasks, and linked
          TSC or CCS competencies. This runs separately from the main learner workflow.
        </p>
        <div className="hero-metrics">
          <div className="metric-pill">
            <span className="metric-label">Roles</span>
            <span className="metric-value">${metrics.roleCount}</span>
          </div>
          <div className="metric-pill">
            <span className="metric-label">Sectors</span>
            <span className="metric-value">${metrics.sectorCount}</span>
          </div>
          <div className="metric-pill">
            <span className="metric-label">Tracks</span>
            <span className="metric-value">${metrics.trackCount}</span>
          </div>
        </div>
        <div className="hero-actions">
          <a className="utility-btn utility-btn-brand hero-link" href="/">Open main academy flow</a>
        </div>
      </header>

      <section className="industry-stage job-role-stage">
        <div className="industry-head">
          <div>
            <h2>Browse Job Roles</h2>
            <p className="selection-sub">
              Filter the framework data by sector, track, or job role name, then inspect the linked skill and competency structure.
            </p>
          </div>
          <p className="panel-status" data-tone=${listStatus.tone || undefined}>${listStatus.text}</p>
        </div>

        <div className="role-toolbar">
          <div className="role-toolbar-field">
            <label className="field-label" htmlFor="role-sector-filter">Sector</label>
            <select
              id="role-sector-filter"
              value=${selectedSector}
              onChange=${(event) => setSelectedSector(event.target.value)}
            >
              <option value="">All sectors</option>
              ${sectorOptions.map((sector) => html`<option key=${sector} value=${sector}>${sector}</option>`)}
            </select>
          </div>
          <div className="role-toolbar-field">
            <label className="field-label" htmlFor="role-track-filter">Track</label>
            <select
              id="role-track-filter"
              value=${selectedTrack}
              onChange=${(event) => setSelectedTrack(event.target.value)}
            >
              <option value="">All tracks</option>
              ${trackOptions.map((track) => html`<option key=${track} value=${track}>${track}</option>`)}
            </select>
          </div>
          <div className="role-toolbar-field role-toolbar-search">
            <label className="field-label" htmlFor="role-search-input">Job role search</label>
            <input
              id="role-search-input"
              type="search"
              placeholder="Search role, track, or sector..."
              value=${roleSearch}
              onInput=${(event) => setRoleSearch(event.target.value)}
            />
          </div>
          <div className="role-toolbar-actions">
            <button type="button" className="utility-btn" onClick=${clearFilters}>Clear filters</button>
            <p className="selection-sub role-filter-summary">
              ${visibleRoles.length} visible role${visibleRoles.length === 1 ? "" : "s"}
            </p>
          </div>
        </div>

        <div className="role-layout">
          <aside className="role-list-panel">
            <div className="role-list-head">
              <p className="selection-label">Job Roles</p>
              <p className="selection-sub">
                ${currentRole ? `${currentRole.sector} · ${currentRole.track}` : "Select a role from the filtered list."}
              </p>
            </div>
            <div className="role-list">
              ${visibleRoles.length
                ? visibleRoles.map((role) => html`
                    <button
                      key=${role.job_role_id}
                      type="button"
                      className=${`role-card ${role.job_role_id === selectedRoleId ? "active" : ""}`}
                      onClick=${() => setSelectedRoleId(role.job_role_id)}
                    >
                      <div className="role-card-top">
                        <p className="role-card-title">${role.job_role_name}</p>
                        ${!role.has_skill_requirements
                          ? html`<span className="role-alert-chip">No skill rows</span>`
                          : null}
                      </div>
                      <p className="role-card-meta">${role.sector} · ${role.track}</p>
                      <div className="role-chip-row">
                        <span className="role-chip">${role.skill_requirement_count} skill row${role.skill_requirement_count === 1 ? "" : "s"}</span>
                        <span className="role-chip">${role.critical_work_function_count} work function${role.critical_work_function_count === 1 ? "" : "s"}</span>
                      </div>
                    </button>
                  `)
                : html`<p className="empty-note">No job roles match the current filters.</p>`}
            </div>
          </aside>

          <section className="role-detail-panel">
            ${roleDetail
              ? html`
                  <div className="role-detail-scroll">
                    <div className="overlay-head role-detail-head">
                      <div>
                        <p className="overlay-kicker">Job Role</p>
                        <h2>${roleDetail.job_role_name}</h2>
                        <p className="selection-sub">${roleDetail.sector} · ${roleDetail.track}</p>
                      </div>
                    </div>

                    <p className="panel-status" data-tone=${detailStatus.tone || undefined}>${detailStatus.text}</p>

                    <div className="role-summary-grid">
                      <article className="map-block role-summary-card">
                        <p className="selection-label">Role Description</p>
                        <p className="role-copy">
                          ${roleDetail.role_description || "No role description provided in the source sheet."}
                        </p>
                      </article>
                      <article className="map-block role-summary-card">
                        <p className="selection-label">Performance Expectation</p>
                        <p className="role-copy">
                          ${roleDetail.performance_expectation || "No performance expectation provided in the source sheet."}
                        </p>
                      </article>
                    </div>

                    <section className="detail-section">
                      <div className="detail-section-head">
                        <div>
                          <p className="selection-label">Critical Work Functions</p>
                          <h3>Role responsibilities</h3>
                        </div>
                      </div>
                      ${roleDetail.critical_work_functions.length
                        ? html`
                            <div className="work-function-grid">
                              ${roleDetail.critical_work_functions.map((workFunction) => html`
                                <article key=${workFunction.name} className="map-block work-function-card">
                                  <p className="role-card-title">${workFunction.name}</p>
                                  ${workFunction.key_tasks.length
                                    ? html`
                                        <ul className="task-list">
                                          ${workFunction.key_tasks.map((task) => html`<li key=${task}>${task}</li>`)}
                                        </ul>
                                      `
                                    : html`<p className="inline-note">No key tasks listed for this work function.</p>`}
                                </article>
                              `)}
                            </div>
                          `
                        : html`<p className="empty-note">No critical work functions were seeded for this role.</p>`}
                    </section>

                    <section className="detail-section">
                      <div className="detail-section-head">
                        <div>
                          <p className="selection-label">Skills and Competencies</p>
                          <h3>Framework-linked requirements</h3>
                        </div>
                      </div>
                      ${roleDetail.skills.length
                        ? html`
                            <div className="role-skill-grid">
                              ${roleDetail.skills.map((skill) => html`
                                <article key=${`${skill.skill_title}-${skill.proficiency_level}-${skill.skill_type}`} className="map-block role-skill-card">
                                  <div className="role-skill-top">
                                    <div>
                                      <p className="role-card-title">${skill.skill_title}</p>
                                      <p className="role-card-meta">
                                        ${(skill.skill_type || "unspecified").toUpperCase()} · Proficiency ${skill.proficiency_level}
                                      </p>
                                    </div>
                                    <span className="role-skill-level">${skill.proficiency_level}</span>
                                  </div>
                                  <p className="role-copy role-skill-description">
                                    ${skill.proficiency_description || "No proficiency description provided."}
                                  </p>
                                  <div className="role-code-list">
                                    ${skill.tsc_ccs_codes.map((code) => html`<span key=${code} className="role-code-chip">${code}</span>`)}
                                  </div>
                                  <div className="competency-columns">
                                    <div className="competency-column">
                                      <p className="selection-label">Knowledge</p>
                                      ${skill.knowledge_items.length
                                        ? html`
                                            <ul className="competency-list">
                                              ${skill.knowledge_items.map((item) => html`<li key=${item}>${item}</li>`)}
                                            </ul>
                                          `
                                        : html`<p className="inline-note">No knowledge items.</p>`}
                                    </div>
                                    <div className="competency-column">
                                      <p className="selection-label">Ability</p>
                                      ${skill.ability_items.length
                                        ? html`
                                            <ul className="competency-list">
                                              ${skill.ability_items.map((item) => html`<li key=${item}>${item}</li>`)}
                                            </ul>
                                          `
                                        : html`<p className="inline-note">No ability items.</p>`}
                                    </div>
                                  </div>
                                </article>
                              `)}
                            </div>
                          `
                        : html`<p className="empty-note">This role has no skill requirement rows in the job role mapping sheet.</p>`}
                    </section>
                  </div>
                `
              : html`
                  <div className="role-detail-empty">
                    <p className="selection-label">Job Role Detail</p>
                    <p className="empty-note">Select a role to load its SkillsFuture mapping.</p>
                    <p className="panel-status" data-tone=${detailStatus.tone || undefined}>${detailStatus.text}</p>
                  </div>
                `}
          </section>
        </div>
      </section>
    </main>
  `;
}

ReactDOM.createRoot(document.getElementById("academy-root")).render(html`<${App} />`);
