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

function getQuizModeLabel(mode) {
  const labels = {
    competency: "Per Competency",
    knowledge: "Knowledge Only",
    ability: "Ability Only",
    proficiency: "Per Proficiency Level",
    skill: "Per Skill"
  };
  return labels[mode] || mode;
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

  const [quizOpen, setQuizOpen] = useState(false);
  const [quizModeSelectionOpen, setQuizModeSelectionOpen] = useState(false);
  const [quizData, setQuizData] = useState(null);
  const [quizMode, setQuizMode] = useState("competency");
  const [quizStatus, setQuizStatus] = useState({ text: "", tone: "" });
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [userAnswers, setUserAnswers] = useState({});
  const [quizSubmitted, setQuizSubmitted] = useState(false);

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

  const loadQuiz = async (mode) => {
    if (!selectedSector || !selectedSkill || !selectedProficiency || !selectedCompetency) {
      setQuizStatus({ text: "Missing required selections.", tone: "error" });
      return;
    }

    setQuizMode(mode);
    setQuizOpen(true);
    setQuizModeSelectionOpen(false);
    setQuizStatus({ text: "Loading quiz...", tone: "" });
    setCurrentQuestionIndex(0);
    setUserAnswers({});
    setQuizSubmitted(false);

    try {
      // Get proficiency description from mapData
      const proficiencyMappings = mapData && Array.isArray(mapData.mappings) ? mapData.mappings : [];
      const selectedEntry = proficiencyMappings.find((entry) => entry.proficiency_level === selectedProficiency) || null;
      const proficiencyDesc = selectedEntry?.proficiency_description || "";

      const payload = {
        sector: selectedSector,
        skill: selectedSkill,
        competency: selectedCompetency,
        proficiency_level: selectedProficiency,
        proficiency_description: proficiencyDesc,
        quiz_mode: mode,
      };

      const quiz = await fetchJson("/api/quiz/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      setQuizData(quiz);
      setQuizStatus({ text: "Quiz loaded successfully.", tone: "success" });
    } catch (error) {
      setQuizStatus({ text: "Quiz not found, please contact admin.", tone: "error" });
    }
  };

  const openQuizModeSelector = () => {
    setQuizModeSelectionOpen(true);
  };

  const closeQuizModeSelection = () => {
    setQuizModeSelectionOpen(false);
  };

  const closeQuiz = () => {
    setQuizOpen(false);
    setQuizData(null);
    setCurrentQuestionIndex(0);
    setUserAnswers({});
    setQuizSubmitted(false);
    setQuizStatus({ text: "", tone: "" });
  };

  const handleAnswerSelect = (answer) => {
    if (!quizSubmitted) {
      const questionKey = `q${currentQuestionIndex}`;
      setUserAnswers({ ...userAnswers, [questionKey]: answer });
    }
  };

  const goToNextQuestion = () => {
    if (currentQuestionIndex < quizData.questions.length - 1) {
      setCurrentQuestionIndex(currentQuestionIndex + 1);
    }
  };

  const goToPreviousQuestion = () => {
    if (currentQuestionIndex > 0) {
      setCurrentQuestionIndex(currentQuestionIndex - 1);
    }
  };

  const submitQuiz = () => {
    const questions = quizData.questions;
    let correctCount = 0;

    questions.forEach((question, index) => {
      const userAnswer = userAnswers[`q${index}`];
      if (userAnswer === question.correct) {
        correctCount++;
      }
    });

    setQuizSubmitted(true);
    const score = Math.round((correctCount / questions.length) * 100);
    setQuizStatus({
      text: `Quiz completed! Score: ${correctCount}/${questions.length} (${score}%)`,
      tone: score >= 70 ? "success" : "error",
    });
  };

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
                  ${selectedSkill && selectedProficiency && selectedCompetency
                    ? html`
                        <button
                          type="button"
                          className="primary-btn quiz-header-btn"
                          onClick=${openQuizModeSelector}
                        >
                          📝 Take Quiz
                        </button>
                      `
                    : html``}
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

      ${quizModeSelectionOpen
        ? html`
            <div className="quiz-backdrop" onClick=${closeQuizModeSelection}>
              <section
                className="quiz-panel"
                role="dialog"
                aria-modal="true"
                aria-label="Quiz Mode Selection"
                onClick=${(event) => event.stopPropagation()}
              >
                <div className="quiz-head">
                  <div>
                    <p className="quiz-kicker">Quiz Type</p>
                    <h2>Select Quiz Mode</h2>
                  </div>
                  <button type="button" className="close-btn" onClick=${closeQuizModeSelection}>Close</button>
                </div>

                <div className="quiz-content">
                  <div className="quiz-mode-list">
                    <button
                      type="button"
                      className="quiz-mode-btn"
                      onClick=${() => loadQuiz("competency")}
                    >
                      <p className="mode-title">Per Competency</p>
                      <p className="mode-desc">Quiz based on selected competency for this level</p>
                    </button>
                    <button
                      type="button"
                      className="quiz-mode-btn"
                      onClick=${() => loadQuiz("knowledge")}
                    >
                      <p className="mode-title">Knowledge Only</p>
                      <p className="mode-desc">Quiz for Knowledge competencies for this level</p>
                    </button>
                    <button
                      type="button"
                      className="quiz-mode-btn"
                      onClick=${() => loadQuiz("ability")}
                    >
                      <p className="mode-title">Ability Only</p>
                      <p className="mode-desc">Quiz for Ability competencies for this level</p>
                    </button>
                    <button
                      type="button"
                      className="quiz-mode-btn"
                      onClick=${() => loadQuiz("proficiency")}
                    >
                      <p className="mode-title">Per Proficiency Level</p>
                      <p className="mode-desc">Knowledge + Ability for this level</p>
                    </button>
                    <button
                      type="button"
                      className="quiz-mode-btn"
                      onClick=${() => loadQuiz("skill")}
                    >
                      <p className="mode-title">Per Skill</p>
                      <p className="mode-desc">All proficiency levels</p>
                    </button>
                  </div>
                </div>
              </section>
            </div>
          `
        : null}

      ${quizOpen
        ? html`
            <div className="quiz-backdrop" onClick=${closeQuiz}>
              <section
                className="quiz-panel"
                role="dialog"
                aria-modal="true"
                aria-label="Quiz"
                onClick=${(event) => event.stopPropagation()}
              >
                <div className="quiz-head">
                  <div>
                    <p className="quiz-kicker">Quiz</p>
                    <h2>${selectedSkill || "Quiz"}</h2>
                    <p className="quiz-mode-info">
                      ${getQuizModeLabel(quizMode)} 
                      ${quizMode === "competency" ? `• ${selectedCompetency}` : ""}
                      ${quizMode === "proficiency" ? `• ${selectedProficiency}` : ""}
                      ${quizMode === "skill" ? `• All Proficiency Levels` : ""}
                    </p>
                  </div>
                  <button type="button" className="close-btn" onClick=${closeQuiz}>Close</button>
                </div>

                <div className="quiz-content">
                  ${quizStatus.text && !quizData
                    ? html`
                        <div className="quiz-loading-state">
                          <p className="panel-status" data-tone=${quizStatus.tone || undefined}>${quizStatus.text}</p>
                          ${quizStatus.tone === "error"
                            ? html`
                                <button type="button" className="primary-btn" onClick=${closeQuiz}>
                                  Close
                                </button>
                              `
                            : html`<div className="spinner"></div>`}
                        </div>
                      `
                    : !quizData
                      ? html`
                          <div className="quiz-loading-state">
                            <div className="spinner"></div>
                            <p>Loading quiz...</p>
                          </div>
                        `
                      : !quizSubmitted
                        ? html`
                            <div className="quiz-question-container">
                              <div className="quiz-progress">
                                <span>Question ${currentQuestionIndex + 1} of ${quizData.questions.length}</span>
                                <div className="progress-bar">
                                  <div 
                                    className="progress-fill" 
                                    style=${{width: `${((currentQuestionIndex + 1) / quizData.questions.length) * 100}%`}}
                                  ></div>
                                </div>
                              </div>

                              <div className="question-block">
                                <p className="question-text">${quizData.questions[currentQuestionIndex].question}</p>

                                <div className="options-block">
                                  ${['A', 'B', 'C', 'D'].map((option) => {
                                    const isSelected = userAnswers[`q${currentQuestionIndex}`] === option;
                                    return html`
                                      <button
                                        key=${`opt-${option}`}
                                        type="button"
                                        className=${`option-btn ${isSelected ? 'selected' : ''}`}
                                        onClick=${() => handleAnswerSelect(option)}
                                      >
                                        <span className="option-label">${option}</span>
                                        <span className="option-text">${quizData.questions[currentQuestionIndex].options[option]}</span>
                                      </button>
                                    `;
                                  })}
                                </div>
                              </div>

                              <div className="quiz-controls">
                                <button
                                  type="button"
                                  className="nav-btn"
                                  onClick=${goToPreviousQuestion}
                                  disabled=${currentQuestionIndex === 0}
                                >
                                  ← Previous
                                </button>

                                ${currentQuestionIndex === quizData.questions.length - 1
                                  ? html`
                                      <button
                                        type="button"
                                        className="primary-btn"
                                        onClick=${submitQuiz}
                                        disabled=${!userAnswers[`q${currentQuestionIndex}`]}
                                      >
                                        Submit Quiz
                                      </button>
                                    `
                                  : html`
                                      <button
                                        type="button"
                                        className="nav-btn"
                                        onClick=${goToNextQuestion}
                                        disabled=${!userAnswers[`q${currentQuestionIndex}`]}
                                      >
                                        Next →
                                      </button>
                                    `}
                              </div>
                            </div>
                          `
                        : html`
                            <div className="quiz-results">
                              <div className="results-summary">
                                <h3>Quiz Completed!</h3>
                                <p className="results-mode">Mode: ${getQuizModeLabel(quizMode)}</p>
                                <p className="panel-status" data-tone=${quizStatus.tone || undefined}>${quizStatus.text}</p>
                              </div>

                              <div className="results-details">
                                ${quizData.questions.map((question, index) => {
                                  const userAnswer = userAnswers[`q${index}`];
                                  const isCorrect = userAnswer === question.correct;
                                  return html`
                                    <div key=${index} className="result-item">
                                      <p className="result-question">${index + 1}. ${question.question}</p>
                                      
                                      <div className="result-options">
                                        ${['A', 'B', 'C', 'D'].map((letter) => {
                                          const optionText = question.options[letter];
                                          const isUserAnswer = letter === userAnswer;
                                          const isCorrectAnswer = letter === question.correct;
                                          
                                          let optionClass = 'result-option';
                                          let indicator = '  ';
                                          
                                          if (isCorrectAnswer) {
                                            optionClass += ' result-option-correct';
                                            indicator = '✓ ';
                                          } else if (isUserAnswer && !isCorrect) {
                                            optionClass += ' result-option-wrong';
                                            indicator = '✗ ';
                                          }
                                          
                                          return html`<p key=${letter} className=${optionClass}>${indicator}${letter}. ${optionText}</p>`;
                                        })}
                                      </div>
                                      
                                      <p className="result-your-answer">
                                        Your answer: <strong>${userAnswer}</strong> 
                                        <span className=${isCorrect ? 'correct' : 'incorrect'}>
                                          ${isCorrect ? '✓ Correct' : '✗ Incorrect'}
                                        </span>
                                      </p>
                                      ${!isCorrect
                                        ? html`<p className="result-correct-answer">Correct answer: <strong>${question.correct}</strong></p>`
                                        : html``}
                                      <p className="result-explanation">
                                        <strong>Explanation:</strong> ${question.explanation}
                                      </p>
                                    </div>
                                  `;
                                })}
                              </div>

                              <button
                                type="button"
                                className="primary-btn"
                                onClick=${closeQuiz}
                              >
                                Close Quiz
                              </button>
                            </div>
                          `}
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
