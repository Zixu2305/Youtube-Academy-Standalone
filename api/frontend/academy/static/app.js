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

function useDebouncedValue(value, delayMs) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

function clampNumber(value, fallback, min, max) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, numeric));
}

function App() {
  const [sectors, setSectors] = useState([]);
  const [selectedSector, setSelectedSector] = useState("");

  const [overlayOpen, setOverlayOpen] = useState(false);
  const [skillSearch, setSkillSearch] = useState("");
  const [skills, setSkills] = useState([]);
  const [selectedSkill, setSelectedSkill] = useState("");
  const [mapData, setMapData] = useState(null);
  const [selectedProficiency, setSelectedProficiency] = useState("");
  const [selectedCompetency, setSelectedCompetency] = useState("");

  const [extraContext, setExtraContext] = useState("");
  const [topK, setTopK] = useState(6);
  const [strictSkillMatch, setStrictSkillMatch] = useState(true);

  const [recommendations, setRecommendations] = useState([]);
  const [hasRecommended, setHasRecommended] = useState(false);

  const [sectorStatus, setSectorStatus] = useState({ text: "Loading industries...", tone: "" });
  const [skillStatus, setSkillStatus] = useState({ text: "Pick an industry to start.", tone: "" });
  const [mapStatus, setMapStatus] = useState({ text: "", tone: "" });
  const [recommendStatus, setRecommendStatus] = useState({ text: "", tone: "" });

  useEffect(() => {
    const loadSectors = async () => {
      setSectorStatus({ text: "Loading industries...", tone: "" });
      try {
        const sectorRows = await fetchJson("/api/public/sectors");
        setSectors(sectorRows);
        if (!sectorRows.length) {
          setSectorStatus({ text: "No industries available.", tone: "error" });
          return;
        }
        setSectorStatus({ text: "Select an industry card to begin.", tone: "success" });
      } catch (error) {
        setSectorStatus({ text: `Unable to load industries: ${error.message}`, tone: "error" });
      }
    };
    loadSectors();
  }, []);

  useEffect(() => {
    document.body.classList.toggle("overlay-open", overlayOpen);
    return () => {
      document.body.classList.remove("overlay-open");
    };
  }, [overlayOpen]);

  const debouncedSkillSearch = useDebouncedValue(skillSearch.trim(), 250);

  useEffect(() => {
    if (!overlayOpen || !selectedSector) {
      return;
    }

    const loadSkills = async () => {
      setSkillStatus({ text: "Loading skills...", tone: "" });
      try {
        const params = new URLSearchParams({
          sector: selectedSector,
          q: debouncedSkillSearch,
          limit: "700",
        });
        const skillRows = await fetchJson(`/api/public/skills?${params.toString()}`);
        setSkills(skillRows);
        setSkillStatus({ text: `${skillRows.length} skill(s) found.`, tone: skillRows.length ? "success" : "" });

        if (selectedSkill && !skillRows.some((item) => item.skill === selectedSkill)) {
          setSelectedSkill("");
          setMapData(null);
          setSelectedProficiency("");
          setSelectedCompetency("");
        }
      } catch (error) {
        setSkillStatus({ text: `Unable to load skills: ${error.message}`, tone: "error" });
      }
    };

    loadSkills();
  }, [overlayOpen, selectedSector, debouncedSkillSearch]);

  useEffect(() => {
    if (!overlayOpen || !selectedSector || !selectedSkill) {
      setMapData(null);
      setMapStatus({ text: "", tone: "" });
      return;
    }

    const loadMapping = async () => {
      setMapStatus({ text: "Loading mapped competencies...", tone: "" });
      try {
        const params = new URLSearchParams({
          sector: selectedSector,
          skill: selectedSkill,
        });
        const mapResponse = await fetchJson(`/api/public/skill-map?${params.toString()}`);
        setMapData(mapResponse);

        const levels = Array.isArray(mapResponse.mappings) ? mapResponse.mappings : [];
        const existingLevel = levels.some((entry) => entry.proficiency_level === selectedProficiency);
        if (!levels.length) {
          setSelectedProficiency("");
          setSelectedCompetency("");
        } else if (!existingLevel) {
          setSelectedProficiency(levels[0].proficiency_level);
          setSelectedCompetency("");
        }

        setMapStatus({ text: `${mapResponse.count} proficiency level(s) mapped.`, tone: "success" });
      } catch (error) {
        setMapData(null);
        setMapStatus({ text: `Unable to load mapping: ${error.message}`, tone: "error" });
      }
    };

    loadMapping();
  }, [overlayOpen, selectedSector, selectedSkill]);

  const openOverlayForSector = (sectorName) => {
    if (!sectorName) {
      return;
    }

    const isNewSector = sectorName !== selectedSector;
    setSelectedSector(sectorName);
    setOverlayOpen(true);
    setSkillSearch("");
    setSkillStatus({ text: "Loading skills...", tone: "" });
    setMapStatus({ text: "", tone: "" });
    setRecommendStatus({ text: "", tone: "" });

    if (isNewSector) {
      setSelectedSkill("");
      setMapData(null);
      setSelectedProficiency("");
      setSelectedCompetency("");
      setExtraContext("");
      setTopK(6);
      setStrictSkillMatch(true);
      setRecommendations([]);
      setHasRecommended(false);
    }
  };

  const closeOverlay = () => setOverlayOpen(false);

  const onSelectSkill = (skillName) => {
    setSelectedSkill(skillName);
    setMapData(null);
    setSelectedProficiency("");
    setSelectedCompetency("");
    setRecommendStatus({ text: "", tone: "" });
  };

  const onSelectProficiency = (proficiencyLevel) => {
    if (proficiencyLevel === selectedProficiency) {
      return;
    }
    setSelectedProficiency(proficiencyLevel);
    setSelectedCompetency("");
    setRecommendStatus({ text: "", tone: "" });
  };

  const onSelectCompetency = (proficiencyLevel, competencyText) => {
    setSelectedProficiency(proficiencyLevel);
    setSelectedCompetency(competencyText);
    setRecommendStatus({ text: "", tone: "" });
  };

  const recommendVideos = async () => {
    if (!selectedSector || !selectedSkill) {
      setRecommendStatus({ text: "Choose an industry and skill first.", tone: "error" });
      return;
    }
    if (!selectedCompetency) {
      setRecommendStatus({ text: "Select one competency first.", tone: "error" });
      return;
    }

    const safeTopK = clampNumber(topK, 6, 1, 20);
    setTopK(safeTopK);
    setHasRecommended(true);
    setRecommendStatus({ text: "Generating recommendations...", tone: "" });

    const promptParts = [selectedCompetency];
    if (extraContext.trim()) {
      promptParts.push(extraContext.trim());
    }

    const payload = {
      sector: selectedSector,
      skill: selectedSkill,
      proficiency_level: selectedProficiency || null,
      competency: promptParts.join(". "),
      top_k: safeTopK,
      strict_skill_match: strictSkillMatch,
    };

    try {
      const response = await fetchJson("/api/public/recommend/videos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setRecommendations(response.results || []);
      setRecommendStatus({ text: `${response.count} recommendation(s) ready.`, tone: "success" });
    } catch (error) {
      setRecommendStatus({ text: `Unable to recommend videos: ${error.message}`, tone: "error" });
    }
  };

  const proficiencyMappings = mapData && Array.isArray(mapData.mappings) ? mapData.mappings : [];
  const selectedMapEntry =
    proficiencyMappings.find((entry) => entry.proficiency_level === selectedProficiency) ||
    proficiencyMappings[0] ||
    null;

  const sectorMetrics = useMemo(
    () => ({
      totalSectors: sectors.length,
      totalSkills: sectors.reduce((total, item) => total + Number(item.skill_count || 0), 0),
    }),
    [sectors],
  );

  const selectionSummary = selectedSkill
    ? `${selectedSector} · ${selectedSkill}${selectedProficiency ? ` · ${selectedProficiency}` : ""}`
    : "No skill path selected yet.";

  const recommendationContent = !hasRecommended
    ? html`<p className="empty-note">Run recommendation after selecting a competency.</p>`
    : recommendations.length
      ? recommendations.map((video) => {
          const url = video.video_id
            ? `https://www.youtube.com/watch?v=${encodeURIComponent(video.video_id)}`
            : "#";
          return html`
            <a
              key=${`${video.video_id}-${video.score}`}
              className="video-card"
              href=${url}
              target="_blank"
              rel="noopener noreferrer"
            >
              ${video.thumbnail_url
                ? html`
                    <img
                      className="video-thumb"
                      src=${video.thumbnail_url}
                      alt=${video.title || "Video thumbnail"}
                      loading="lazy"
                    />
                  `
                : html`<div className="video-thumb"></div>`}
              <div>
                <p className="video-title">${video.title || "Untitled video"}</p>
                <p className="video-meta">
                  ${(video.channel_title || "Unknown channel") +
                  " · " +
                  (video.duration || "n/a") +
                  " · score " +
                  Number(video.score).toFixed(3)}
                </p>
                <span className="video-chip">${video.skill_name || selectedSkill || "Skill"}</span>
              </div>
            </a>
          `;
        })
      : html`<p className="empty-note">No recommendations matched the current filters.</p>`;

  return html`
    <main className="academy-shell">
      <header className="hero">
        <p className="eyebrow">Learner Catalog Prototype</p>
        <h1>Skill Atlas and Guided Video Discovery</h1>
        <p className="hero-copy">
          Start from industry-level exploration. Open one overlay flow to choose skill, proficiency,
          and competency before pulling recommendations.
        </p>
        <div className="hero-metrics">
          <div className="metric-pill">
            <span className="metric-label">Industries</span>
            <span className="metric-value">${sectorMetrics.totalSectors}</span>
          </div>
          <div className="metric-pill">
            <span className="metric-label">Mapped Skills</span>
            <span className="metric-value">${sectorMetrics.totalSkills}</span>
          </div>
        </div>
      </header>

      <section className="industry-stage">
        <div className="industry-head">
          <h2>Choose an Industry</h2>
          <p className="panel-status" data-tone=${sectorStatus.tone || undefined}>${sectorStatus.text}</p>
        </div>
        <div className="industry-grid">
          ${sectors.length
            ? sectors.map((sector, index) => html`
                <button
                  key=${sector.sector}
                  type="button"
                  className=${`sector-card tone-${index % 6} ${sector.sector === selectedSector ? "active" : ""}`}
                  onClick=${() => openOverlayForSector(sector.sector)}
                >
                  <div className="sector-top">
                    <p className="sector-title">${sector.sector}</p>
                    <span className="sector-count">${sector.skill_count} skills</span>
                  </div>
                  <p className="sector-cta">Open guided selector</p>
                </button>
              `)
            : html`<p className="empty-note">No industries available.</p>`}
        </div>
      </section>

      ${overlayOpen
        ? html`
            <div className="overlay-backdrop" onClick=${closeOverlay}>
              <section
                className="overlay-panel"
                role="dialog"
                aria-modal="true"
                aria-label="Skill path selector"
                onClick=${(event) => event.stopPropagation()}
              >
                <div className="overlay-head">
                  <div>
                    <p className="overlay-kicker">Industry</p>
                    <h2>${selectedSector || "Select an industry"}</h2>
                  </div>
                  <button type="button" className="close-btn" onClick=${closeOverlay}>Close</button>
                </div>

                <div className="overlay-grid">
                  <aside className="picker-col">
                    <label className="field-label" htmlFor="skill-search">Find Skill</label>
                    <input
                      id="skill-search"
                      type="search"
                      placeholder="Type to filter skills..."
                      value=${skillSearch}
                      onInput=${(event) => setSkillSearch(event.target.value)}
                    />
                    <p className="panel-status" data-tone=${skillStatus.tone || undefined}>${skillStatus.text}</p>
                    <div className="skill-list">
                      ${skills.length
                        ? skills.map((item) => html`
                            <button
                              key=${item.skill}
                              type="button"
                              className=${`skill-item ${item.skill === selectedSkill ? "active" : ""}`}
                              onClick=${() => onSelectSkill(item.skill)}
                            >
                              <p className="skill-title">${item.skill}</p>
                              <p className="skill-meta">${item.mapped_proficiency_count} mapped level(s)</p>
                            </button>
                          `)
                        : html`<p className="empty-note">No skills match this filter.</p>`}
                    </div>
                  </aside>

                  <div className="detail-col">
                    ${selectedSkill
                      ? html`
                          <div className="detail-layout">
                            <div className="detail-main">
                              <div className="map-block">
                                <p className="selected-skill">Skill: ${selectedSkill}</p>
                                <p className="panel-status" data-tone=${mapStatus.tone || undefined}>${mapStatus.text}</p>

                                ${selectedMapEntry
                                  ? html`
                                      <div className="level-row" role="tablist" aria-label="Proficiency levels">
                                        ${proficiencyMappings.map((entry) => html`
                                          <button
                                            key=${`level-${entry.proficiency_level}`}
                                            type="button"
                                            className=${`level-pill ${entry.proficiency_level === selectedMapEntry.proficiency_level ? "active" : ""}`}
                                            onClick=${() => onSelectProficiency(entry.proficiency_level)}
                                            aria-pressed=${entry.proficiency_level === selectedMapEntry.proficiency_level}
                                          >
                                            ${entry.proficiency_level}
                                          </button>
                                        `)}
                                      </div>

                                      <p className="map-desc">
                                        ${selectedMapEntry.proficiency_description || "No proficiency description provided."}
                                      </p>

                                      <p className="map-group-title">Knowledge Competencies</p>
                                      <div className="chip-row">
                                        ${selectedMapEntry.knowledge_items.length
                                          ? selectedMapEntry.knowledge_items.map((item) => {
                                              const text = `knowledge: ${item}`;
                                              const active =
                                                selectedMapEntry.proficiency_level === selectedProficiency &&
                                                selectedCompetency === text;
                                              return html`
                                                <button
                                                  key=${`k-${selectedMapEntry.proficiency_level}-${item}`}
                                                  type="button"
                                                  className=${`chip ${active ? "active" : ""}`}
                                                  onClick=${() =>
                                                    onSelectCompetency(selectedMapEntry.proficiency_level, text)}
                                                >
                                                  ${text}
                                                </button>
                                              `;
                                            })
                                          : html`<span className="inline-note">No mapped knowledge items.</span>`}
                                      </div>

                                      <p className="map-group-title">Ability Competencies</p>
                                      <div className="chip-row">
                                        ${selectedMapEntry.ability_items.length
                                          ? selectedMapEntry.ability_items.map((item) => {
                                              const text = `ability: ${item}`;
                                              const active =
                                                selectedMapEntry.proficiency_level === selectedProficiency &&
                                                selectedCompetency === text;
                                              return html`
                                                <button
                                                  key=${`a-${selectedMapEntry.proficiency_level}-${item}`}
                                                  type="button"
                                                  className=${`chip ${active ? "active" : ""}`}
                                                  onClick=${() =>
                                                    onSelectCompetency(selectedMapEntry.proficiency_level, text)}
                                                >
                                                  ${text}
                                                </button>
                                              `;
                                            })
                                          : html`<span className="inline-note">No mapped ability items.</span>`}
                                      </div>
                                    `
                                  : html`<p className="empty-note">Choose a skill to view mapped details.</p>`}
                              </div>
                            </div>

                            <aside className="result-panel">
                              <p className="selection-label">Current Path</p>
                              <p className="selection-value">${selectionSummary}</p>
                              ${selectedCompetency
                                ? html`<p className="selection-sub">${selectedCompetency}</p>`
                                : html`<p className="selection-sub">Select one competency to complete the path.</p>`}

                              <div className="controls-card">
                                <label className="field-label" htmlFor="extra-context">Additional context (optional)</label>
                                <textarea
                                  id="extra-context"
                                  rows="3"
                                  placeholder="Example: beginner-friendly walkthrough with practical use cases."
                                  value=${extraContext}
                                  onInput=${(event) => setExtraContext(event.target.value)}
                                ></textarea>

                                <div className="controls-row">
                                  <div>
                                    <label className="field-label" htmlFor="top-k">Videos</label>
                                    <input
                                      id="top-k"
                                      type="number"
                                      min="1"
                                      max="20"
                                      value=${topK}
                                      onInput=${(event) => setTopK(event.target.value)}
                                    />
                                  </div>
                                  <label className="checkbox-row" htmlFor="strict-match">
                                    <input
                                      id="strict-match"
                                      type="checkbox"
                                      checked=${strictSkillMatch}
                                      onChange=${(event) => setStrictSkillMatch(event.target.checked)}
                                    />
                                    Strict skill filter
                                  </label>
                                </div>

                                <button
                                  type="button"
                                  className="primary-btn"
                                  onClick=${recommendVideos}
                                  disabled=${!selectedCompetency}
                                >
                                  Recommend Videos
                                </button>
                              </div>

                              <p className="panel-status" data-tone=${recommendStatus.tone || undefined}>${recommendStatus.text}</p>
                              <div className="recommendation-list">${recommendationContent}</div>
                            </aside>
                          </div>
                        `
                      : html`<p className="empty-note">Select a skill to reveal proficiency and competency options.</p>`}
                  </div>
                </div>
              </section>
            </div>
          `
        : null}
    </main>
  `;
}

const rootElement = document.getElementById("academy-root");
const root = ReactDOM.createRoot(rootElement);
root.render(html`<${App} />`);
