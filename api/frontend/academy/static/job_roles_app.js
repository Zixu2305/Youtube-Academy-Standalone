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
  const [activeTab, setActiveTab] = useState("overview");
  const [selectedSkillDetail, setSelectedSkillDetail] = useState(null);
  const [selectedSkillLevel, setSelectedSkillLevel] = useState("");
  const [expandedSummary, setExpandedSummary] = useState({
    description: false,
    expectation: false,
  });
  const [expandedWorkFunctions, setExpandedWorkFunctions] = useState({});
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
      setSelectedSkillDetail(null);
      try {
        const detail = await fetchJson(`/api/public/job-roles/${encodeURIComponent(selectedRoleId)}`);
        if (cancelled) {
          return;
        }
        setRoleDetail(detail);
        setActiveTab("overview");
        setSelectedSkillLevel("");
        setExpandedSummary({ description: false, expectation: false });
        setExpandedWorkFunctions({});
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

  const previewText = (value, maxLength = 220) => {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (!text) {
      return "";
    }
    return text.length > maxLength ? `${text.slice(0, maxLength).trim()}...` : text;
  };

  const isLongText = (value, maxLength) => String(value || "").replace(/\s+/g, " ").trim().length > maxLength;

  const toggleSummary = (key) => {
    setExpandedSummary((current) => ({
      ...current,
      [key]: !current[key],
    }));
  };

  const toggleWorkFunction = (name) => {
    setExpandedWorkFunctions((current) => ({
      ...current,
      [name]: !current[name],
    }));
  };

  const keyItems = (items, count = 3) => (Array.isArray(items) ? items.filter(Boolean).slice(0, count) : []);

  const skillKey = (skill) => `${skill.skill_title}-${skill.proficiency_level}-${skill.skill_type}`;

  const levelSortValue = (level) => {
    const numeric = Number(String(level || "").match(/\d+/)?.[0] || 999);
    return Number.isFinite(numeric) ? numeric : 999;
  };

  const skillLevels = useMemo(() => {
    const skills = Array.isArray(roleDetail?.skills) ? roleDetail.skills : [];
    return Array.from(new Set(skills.map((skill) => String(skill.proficiency_level || "").trim()).filter(Boolean)))
      .sort((left, right) => levelSortValue(left) - levelSortValue(right) || left.localeCompare(right));
  }, [roleDetail]);

  const visibleRoleSkills = useMemo(() => {
    const skills = Array.isArray(roleDetail?.skills) ? roleDetail.skills : [];
    return skills
      .filter((skill) => !selectedSkillLevel || String(skill.proficiency_level) === selectedSkillLevel)
      .slice()
      .sort((left, right) => (
        levelSortValue(left.proficiency_level) - levelSortValue(right.proficiency_level) ||
        String(left.skill_title || "").localeCompare(String(right.skill_title || ""))
      ));
  }, [roleDetail, selectedSkillLevel]);

  const detailMetrics = useMemo(() => {
    const skills = Array.isArray(roleDetail?.skills) ? roleDetail.skills : [];
    const workFunctions = Array.isArray(roleDetail?.critical_work_functions) ? roleDetail.critical_work_functions : [];
    return {
      workFunctions: workFunctions.length,
      skills: skills.length,
      knowledge: skills.reduce((total, skill) => total + (skill.knowledge_items?.length || 0), 0),
      ability: skills.reduce((total, skill) => total + (skill.ability_items?.length || 0), 0),
      levels: new Set(skills.map((skill) => String(skill.proficiency_level || "").trim()).filter(Boolean)).size,
    };
  }, [roleDetail]);

  const metrics = useMemo(() => {
    const sectorCount = sectorOptions.length;
    const trackCount = Array.from(new Set(roles.map((role) => `${role.sector}::${role.track}`))).length;
    return {
      roleCount: roles.length,
      sectorCount,
      trackCount,
    };
  }, [roles, sectorOptions]);

  const resetRoleSelection = () => {
    setSelectedRoleId(null);
    setRoleDetail(null);
    setSelectedSkillDetail(null);
    setActiveTab("overview");
    setExpandedSummary({ description: false, expectation: false });
    setExpandedWorkFunctions({});
    setSelectedSkillLevel("");
  };

  const clearFilters = () => {
    setSelectedSector("");
    setSelectedTrack("");
    setRoleSearch("");
    resetRoleSelection();
  };

  const onSectorChange = (value) => {
    setSelectedSector(value);
    setSelectedTrack("");
    resetRoleSelection();
  };

  const onTrackChange = (value) => {
    setSelectedTrack(value);
    resetRoleSelection();
  };

  const roleListScope = `${selectedSector || "All sectors"} · ${selectedTrack || "All tracks"}`;

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
              onChange=${(event) => onSectorChange(event.target.value)}
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
              onChange=${(event) => onTrackChange(event.target.value)}
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
                ${roleListScope}
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

                    <div className="role-tabs" role="tablist" aria-label="Job role detail sections">
                      ${[
                        ["overview", "Overview"],
                        ["work", "Work Functions"],
                        ["skills", "Skills & Competencies"],
                      ].map(([tab, label]) => html`
                        <button
                          key=${tab}
                          type="button"
                          className=${`role-tab ${activeTab === tab ? "active" : ""}`}
                          onClick=${() => setActiveTab(tab)}
                          aria-selected=${activeTab === tab}
                        >
                          ${label}
                        </button>
                      `)}
                    </div>

                    ${activeTab === "overview"
                      ? html`
                          <section className="detail-section">
                            <div className="role-summary-grid compact">
                              <article className="map-block role-summary-card">
                                <p className="selection-label">Role Description</p>
                                <p className="role-copy">
                                  ${(expandedSummary.description
                                    ? roleDetail.role_description
                                    : previewText(roleDetail.role_description, 300)) || "No role description provided in the source sheet."}
                                </p>
                                ${isLongText(roleDetail.role_description, 300)
                                  ? html`
                                      <button
                                        type="button"
                                        className="read-more-btn"
                                        onClick=${() => toggleSummary("description")}
                                      >
                                        ${expandedSummary.description ? "Show less" : "Read more"}
                                      </button>
                                    `
                                  : null}
                              </article>
                              <article className="map-block role-summary-card">
                                <p className="selection-label">Performance Expectation</p>
                                <p className="role-copy">
                                  ${(expandedSummary.expectation
                                    ? roleDetail.performance_expectation
                                    : previewText(roleDetail.performance_expectation, 260)) || "No performance expectation provided in the source sheet."}
                                </p>
                                ${isLongText(roleDetail.performance_expectation, 260)
                                  ? html`
                                      <button
                                        type="button"
                                        className="read-more-btn"
                                        onClick=${() => toggleSummary("expectation")}
                                      >
                                        ${expandedSummary.expectation ? "Show less" : "Read more"}
                                      </button>
                                    `
                                  : null}
                              </article>
                            </div>

                            <div className="detail-section-head">
                              <div>
                                <p className="selection-label">Framework Snapshot</p>
                                <h3>Mapped scope</h3>
                              </div>
                            </div>
                            <div className="overview-metric-grid">
                              <article className="overview-metric-card">
                                <span className="metric-value">${detailMetrics.workFunctions}</span>
                                <span className="metric-label">Work functions</span>
                              </article>
                              <article className="overview-metric-card">
                                <span className="metric-value">${detailMetrics.skills}</span>
                                <span className="metric-label">Skill groups</span>
                              </article>
                              <article className="overview-metric-card">
                                <span className="metric-value">${detailMetrics.levels}</span>
                                <span className="metric-label">Levels</span>
                              </article>
                              <article className="overview-metric-card">
                                <span className="metric-value">${detailMetrics.knowledge}</span>
                                <span className="metric-label">Knowledge items</span>
                              </article>
                              <article className="overview-metric-card">
                                <span className="metric-value">${detailMetrics.ability}</span>
                                <span className="metric-label">Ability items</span>
                              </article>
                            </div>
                          </section>
                        `
                      : null}

                    ${activeTab === "work"
                      ? html`
                          <section className="detail-section">
                            <div className="detail-section-head">
                              <div>
                                <p className="selection-label">Work Functions</p>
                                <h3>Responsibilities and key tasks</h3>
                              </div>
                            </div>
                            ${roleDetail.critical_work_functions.length
                              ? html`
                                  <div className="work-function-grid">
                                    ${roleDetail.critical_work_functions.map((workFunction) => {
                                      const open = Boolean(expandedWorkFunctions[workFunction.name]);
                                      return html`
                                        <article key=${workFunction.name} className="map-block work-function-card accordion">
                                          <button
                                            type="button"
                                            className="work-function-toggle"
                                            onClick=${() => toggleWorkFunction(workFunction.name)}
                                            aria-expanded=${open}
                                          >
                                            <span className="role-card-title">${workFunction.name}</span>
                                            <span className="work-function-count">
                                              ${workFunction.key_tasks.length} task${workFunction.key_tasks.length === 1 ? "" : "s"}
                                            </span>
                                          </button>
                                          ${open
                                            ? workFunction.key_tasks.length
                                              ? html`
                                                  <div className="topic-chip-row">
                                                    ${workFunction.key_tasks.map((task) => html`<span key=${task} className="topic-chip">${task}</span>`)}
                                                  </div>
                                                `
                                              : html`<p className="inline-note">No key tasks listed for this work function.</p>`
                                            : null}
                                        </article>
                                      `;
                                    })}
                                  </div>
                                `
                              : html`<p className="empty-note">No critical work functions were seeded for this role.</p>`}
                          </section>
                        `
                      : null}

                    ${activeTab === "skills"
                      ? html`
                          <section className="detail-section">
                            <div className="detail-section-head">
                              <div>
                                <p className="selection-label">Skills and Competencies</p>
                                <h3>Framework-linked requirements</h3>
                              </div>
                            </div>
                            <div className="level-filter-row" aria-label="Filter skills by proficiency level">
                              <button
                                type="button"
                                className=${`level-filter-chip ${selectedSkillLevel ? "" : "active"}`}
                                onClick=${() => setSelectedSkillLevel("")}
                              >
                                All levels
                              </button>
                              ${skillLevels.map((level) => html`
                                <button
                                  key=${level}
                                  type="button"
                                  className=${`level-filter-chip ${selectedSkillLevel === level ? "active" : ""}`}
                                  onClick=${() => setSelectedSkillLevel(level)}
                                >
                                  Level ${level}
                                </button>
                              `)}
                              <span className="selection-sub level-filter-count">
                                ${visibleRoleSkills.length} skill${visibleRoleSkills.length === 1 ? "" : "s"}
                              </span>
                            </div>
                            ${roleDetail.skills.length
                              ? html`
                                  <div className="role-skill-grid compact">
                                    ${visibleRoleSkills.map((skill) => {
                                      const knowledgePreview = keyItems(skill.knowledge_items, 2);
                                      const abilityPreview = keyItems(skill.ability_items, 2);
                                      return html`
                                        <article key=${skillKey(skill)} className="map-block role-skill-card compact">
                                          <div className="role-skill-top">
                                            <div>
                                              <p className="role-card-title role-skill-title">${skill.skill_title}</p>
                                              <p className="role-card-meta">${(skill.skill_type || "unspecified").toUpperCase()}</p>
                                            </div>
                                            <span className="role-skill-level">Level ${skill.proficiency_level}</span>
                                          </div>
                                          <p className="role-copy role-skill-description">
                                            ${previewText(skill.proficiency_description, 130) || "No proficiency description provided."}
                                          </p>
                                          <div className="role-code-list">
                                            ${skill.tsc_ccs_codes.slice(0, 3).map((code) => html`<span key=${code} className="role-code-chip">${code}</span>`)}
                                          </div>
                                          <div className="competency-preview-grid">
                                            <span className="topic-chip">${skill.knowledge_items.length} knowledge</span>
                                            <span className="topic-chip">${skill.ability_items.length} ability</span>
                                          </div>
                                          <button
                                            type="button"
                                            className="utility-btn role-detail-btn"
                                            onClick=${() => setSelectedSkillDetail(skill)}
                                          >
                                            View details
                                          </button>
                                        </article>
                                      `;
                                    })}
                                  </div>
                                `
                              : html`<p className="empty-note">This role has no skill requirement rows in the job role mapping sheet.</p>`}
                          </section>
                        `
                      : null}

                    <section className="detail-section legacy-skill-section">
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

      ${selectedSkillDetail
        ? html`
            <div className="role-detail-drawer-backdrop" onClick=${() => setSelectedSkillDetail(null)}>
              <aside
                className="role-detail-drawer"
                role="dialog"
                aria-modal="true"
                aria-label="Skill competency details"
                onClick=${(event) => event.stopPropagation()}
              >
                <div className="drawer-head">
                  <div>
                    <p className="selection-label">${(selectedSkillDetail.skill_type || "skill").toUpperCase()}</p>
                    <h3>${selectedSkillDetail.skill_title}</h3>
                    <p className="role-card-meta">Proficiency level ${selectedSkillDetail.proficiency_level}</p>
                  </div>
                  <button type="button" className="close-btn" onClick=${() => setSelectedSkillDetail(null)}>Close</button>
                </div>

                <div className="drawer-content">
                  <p className="role-copy">${selectedSkillDetail.proficiency_description || "No proficiency description provided."}</p>
                  <div className="role-code-list">
                    ${selectedSkillDetail.tsc_ccs_codes.map((code) => html`<span key=${code} className="role-code-chip">${code}</span>`)}
                  </div>

                  <section className="drawer-section">
                    <p className="selection-label">Knowledge</p>
                    ${selectedSkillDetail.knowledge_items.length
                      ? html`
                          <div className="drawer-chip-list">
                            ${selectedSkillDetail.knowledge_items.map((item) => html`<span key=${item} className="topic-chip">${item}</span>`)}
                          </div>
                        `
                      : html`<p className="inline-note">No knowledge items.</p>`}
                  </section>

                  <section className="drawer-section">
                    <p className="selection-label">Ability</p>
                    ${selectedSkillDetail.ability_items.length
                      ? html`
                          <div className="drawer-chip-list">
                            ${selectedSkillDetail.ability_items.map((item) => html`<span key=${item} className="topic-chip">${item}</span>`)}
                          </div>
                        `
                      : html`<p className="inline-note">No ability items.</p>`}
                  </section>
                </div>
              </aside>
            </div>
          `
        : null}
    </main>
  `;
}

ReactDOM.createRoot(document.getElementById("academy-root")).render(html`<${App} />`);
