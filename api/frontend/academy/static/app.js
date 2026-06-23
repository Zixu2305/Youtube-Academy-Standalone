const { useEffect, useMemo, useState } = React;
const html = htm.bind(React.createElement);

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    let payload = null;
    try {
      payload = await response.json();
      if (payload && payload.detail) {
        detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
      }
    } catch (_) {
      // Keep fallback detail.
    }
    const error = new Error(detail);
    error.status = response.status;
    error.payload = payload;
    throw error;
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

function normalizeSearchText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function tokenizeSearchText(value) {
  const normalized = normalizeSearchText(value);
  return normalized ? normalized.split(/\s+/).filter(Boolean) : [];
}

function isOrderedSubsequence(query, target) {
  if (!query || !target) {
    return false;
  }

  let queryIndex = 0;
  for (const character of target) {
    if (character === query[queryIndex]) {
      queryIndex += 1;
      if (queryIndex === query.length) {
        return true;
      }
    }
  }
  return false;
}

function levenshteinDistance(source, target) {
  if (source === target) {
    return 0;
  }
  if (!source) {
    return target.length;
  }
  if (!target) {
    return source.length;
  }

  let previousRow = Array.from({ length: target.length + 1 }, (_, index) => index);
  for (let sourceIndex = 0; sourceIndex < source.length; sourceIndex += 1) {
    const currentRow = [sourceIndex + 1];
    for (let targetIndex = 0; targetIndex < target.length; targetIndex += 1) {
      const substitutionCost = source[sourceIndex] === target[targetIndex] ? 0 : 1;
      currentRow[targetIndex + 1] = Math.min(
        currentRow[targetIndex] + 1,
        previousRow[targetIndex + 1] + 1,
        previousRow[targetIndex] + substitutionCost,
      );
    }
    previousRow = currentRow;
  }

  return previousRow[target.length];
}

function similarityRatio(source, target) {
  const longestLength = Math.max(source.length, target.length);
  if (!longestLength) {
    return 1;
  }
  return 1 - levenshteinDistance(source, target) / longestLength;
}

function scoreSearchToken(queryToken, candidateToken) {
  if (!queryToken || !candidateToken) {
    return 0;
  }
  if (candidateToken === queryToken) {
    return 1;
  }
  if (candidateToken.startsWith(queryToken)) {
    return Math.max(0.82, 0.98 - (candidateToken.length - queryToken.length) * 0.03);
  }
  if (candidateToken.includes(queryToken)) {
    return Math.max(0.7, 0.84 - candidateToken.indexOf(queryToken) * 0.02);
  }
  if (queryToken.length >= 3 && isOrderedSubsequence(queryToken, candidateToken)) {
    return Math.max(0.58, 0.72 - Math.max(0, candidateToken.length - queryToken.length) * 0.02);
  }
  if (queryToken.length >= 4 && candidateToken.length > queryToken.length) {
    const sharedPrefixLength = Math.min(4, queryToken.length);
    const candidatePrefix = candidateToken.slice(0, queryToken.length + 1);
    const prefixRatio = similarityRatio(queryToken, candidatePrefix);
    if (candidateToken.startsWith(queryToken.slice(0, sharedPrefixLength)) && prefixRatio >= 0.68) {
      return Math.min(0.78, prefixRatio + 0.04);
    }
  }
  if (queryToken.length >= 4) {
    const ratio = similarityRatio(queryToken, candidateToken);
    if (ratio >= 0.72) {
      return ratio * 0.84;
    }
  }
  return 0;
}

function scoreSectorMatch(query, sectorName) {
  const normalizedQuery = normalizeSearchText(query);
  const normalizedSector = normalizeSearchText(sectorName);
  if (!normalizedQuery) {
    return { matched: true, score: 1 };
  }
  if (!normalizedSector) {
    return { matched: false, score: 0 };
  }

  const sectorTokens = tokenizeSearchText(normalizedSector);
  const compactQuery = normalizedQuery.replace(/\s+/g, "");
  const compactSector = normalizedSector.replace(/\s+/g, "");
  const rawQueryTokens = tokenizeSearchText(normalizedQuery);
  const queryTokens = rawQueryTokens.filter((token) => token.length > 1);
  const tokensToScore = queryTokens.length ? queryTokens : [normalizedQuery];

  let phraseScore = 0;
  if (normalizedSector === normalizedQuery) {
    phraseScore = 5.2;
  } else if (normalizedSector.startsWith(normalizedQuery)) {
    phraseScore = 4.4;
  } else if (normalizedSector.includes(normalizedQuery)) {
    phraseScore = 3.6;
  } else if (compactQuery.length >= 3 && isOrderedSubsequence(compactQuery, compactSector)) {
    phraseScore = 2.6;
  }

  const tokenScores = tokensToScore.map((queryToken) => {
    const candidates = [normalizedSector, ...sectorTokens];
    return candidates.reduce((bestScore, candidate) => Math.max(bestScore, scoreSearchToken(queryToken, candidate)), 0);
  });
  const averageTokenScore = tokenScores.length
    ? tokenScores.reduce((sum, value) => sum + value, 0) / tokenScores.length
    : 0;
  const strongTokenMatches = tokenScores.filter((value) => value >= 0.7).length;
  const compactSimilarity = compactQuery.length >= 4 ? similarityRatio(compactQuery, compactSector) : 0;

  const matched =
    phraseScore >= 3.6 ||
    averageTokenScore >= 0.68 ||
    (tokensToScore.length > 1 && strongTokenMatches >= Math.max(1, tokensToScore.length - 1)) ||
    compactSimilarity >= 0.74;

  const score =
    phraseScore +
    averageTokenScore * 4 +
    strongTokenMatches * 0.35 +
    compactSimilarity * 1.8;

  return { matched, score: matched ? score : 0 };
}

function createEmptyRecommendMeta() {
  return {
    query: "",
    appliedFilters: {},
    count: 0,
    usedStrictSkillMatch: true,
  };
}

function createEmptyAdminMapping() {
  return {
    sector: "",
    skill: "",
    proficiency_level: "",
    competency: "",
    item_type: "knowledge",
    proficiency_description: "",
    confidence: 1,
    reason: "Admin reviewed",
    source: "admin",
  };
}

function App() {
  const [sectors, setSectors] = useState([]);
  const [selectedSector, setSelectedSector] = useState("");
  const [sectorSearch, setSectorSearch] = useState("");
  const [skillSuggestions, setSkillSuggestions] = useState([]);
  const [skillSuggestionStatus, setSkillSuggestionStatus] = useState({ text: "", tone: "" });

  const [overlayOpen, setOverlayOpen] = useState(false);
  const [overlayStep, setOverlayStep] = useState("skills");
  const [videoFinderOpen, setVideoFinderOpen] = useState(false);
  const [videoFinderMode, setVideoFinderMode] = useState("path");
  const [directSearchSource, setDirectSearchSource] = useState("library");
  const [directSearchQuery, setDirectSearchQuery] = useState("");
  const [directResultsPage, setDirectResultsPage] = useState(1);
  const [skillSearch, setSkillSearch] = useState("");
  const [skills, setSkills] = useState([]);
  const [selectedSkill, setSelectedSkill] = useState("");
  const [mapData, setMapData] = useState(null);
  const [mapReloadKey, setMapReloadKey] = useState(0);
  const [selectedProficiency, setSelectedProficiency] = useState("");
  const [selectedCompetency, setSelectedCompetency] = useState("");
  const [competencySectionsOpen, setCompetencySectionsOpen] = useState({
    knowledge: false,
    ability: false,
  });

  const [extraContext, setExtraContext] = useState("");
  const [topK, setTopK] = useState(6);
  const [strictSkillMatch, setStrictSkillMatch] = useState(true);

  const [recommendations, setRecommendations] = useState([]);
  const [recommendMeta, setRecommendMeta] = useState(createEmptyRecommendMeta());
  const [hasRecommended, setHasRecommended] = useState(false);
  const [previewVideos, setPreviewVideos] = useState([]);
  const [hasPreviewedVideos, setHasPreviewedVideos] = useState(false);
  const [previewSelection, setPreviewSelection] = useState({});
  const [previewMeta, setPreviewMeta] = useState({
    query: "",
    alreadyIngestedCount: 0,
    quotaExceeded: false,
    quotaMessage: "",
    quota: null,
  });
  const [previewBusy, setPreviewBusy] = useState(false);
  const [ingestBusy, setIngestBusy] = useState(false);
  const [previewSessionUnits, setPreviewSessionUnits] = useState(0);

  const [sectorStatus, setSectorStatus] = useState({ text: "Loading industries...", tone: "" });
  const [skillStatus, setSkillStatus] = useState({ text: "Pick an industry to start.", tone: "" });
  const [mapStatus, setMapStatus] = useState({ text: "", tone: "" });
  const [recommendStatus, setRecommendStatus] = useState({ text: "", tone: "" });
  const [ingestionStatus, setIngestionStatus] = useState({ text: "", tone: "" });

  const [quizOpen, setQuizOpen] = useState(false);
  const [quizModeSelectionOpen, setQuizModeSelectionOpen] = useState(false);
  const [quizData, setQuizData] = useState(null);
  const [quizMode, setQuizMode] = useState("competency");
  const [quizStatus, setQuizStatus] = useState({ text: "", tone: "" });
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [userAnswers, setUserAnswers] = useState({});
  const [quizSubmitted, setQuizSubmitted] = useState(false);

  const [votes, setVotes] = useState({});
  const [userVotes, setUserVotes] = useState({});

  const [adminOpen, setAdminOpen] = useState(false);
  const [adminTab, setAdminTab] = useState("pending");
  const [adminRequests, setAdminRequests] = useState([]);
  const [adminSelectedRequest, setAdminSelectedRequest] = useState(null);
  const [adminMappings, setAdminMappings] = useState([]);
  const [adminStatus, setAdminStatus] = useState({ text: "", tone: "" });
  const [adminBusy, setAdminBusy] = useState(false);
  const [adminDirectQuery, setAdminDirectQuery] = useState("");
  const [adminDirectVideos, setAdminDirectVideos] = useState([]);
  const [adminDirectSelection, setAdminDirectSelection] = useState({});

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
    document.body.classList.toggle("overlay-open", overlayOpen || videoFinderOpen);
    return () => {
      document.body.classList.remove("overlay-open");
    };
  }, [overlayOpen, videoFinderOpen]);

  const debouncedSectorSearch = useDebouncedValue(sectorSearch.trim(), 120);
  const debouncedSkillSearch = useDebouncedValue(skillSearch.trim(), 250);

  useEffect(() => {
    const query = debouncedSectorSearch;
    if (!query || query.length < 2) {
      setSkillSuggestions([]);
      setSkillSuggestionStatus({ text: "", tone: "" });
      return undefined;
    }

    const controller = new AbortController();
    let cancelled = false;

    const loadSkillSuggestions = async () => {
      setSkillSuggestionStatus({ text: "Searching mapped skills across industries...", tone: "" });
      try {
        const params = new URLSearchParams({
          q: query,
          limit: "6",
        });
        const suggestionRows = await fetchJson(`/api/public/skill-suggestions?${params.toString()}`, {
          signal: controller.signal,
        });
        if (cancelled) {
          return;
        }

        setSkillSuggestions(suggestionRows);
        setSkillSuggestionStatus(
          suggestionRows.length
            ? { text: `${suggestionRows.length} skill suggestion(s) mapped across industries.`, tone: "success" }
            : { text: "", tone: "" },
        );
      } catch (error) {
        if (cancelled || error.name === "AbortError") {
          return;
        }
        setSkillSuggestions([]);
        setSkillSuggestionStatus({
          text: `Unable to search mapped skills: ${error.message}`,
          tone: "error",
        });
      }
    };

    loadSkillSuggestions();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [debouncedSectorSearch]);

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
  }, [overlayOpen, selectedSector, selectedSkill, mapReloadKey]);

  const loadQuiz = async (mode) => {
    if (!selectedSector || !selectedSkill) {
      setQuizStatus({ text: "Choose an industry and skill first.", tone: "error" });
      return;
    }
    if (mode === "competency" && !selectedCompetency) {
      setQuizStatus({ text: "Select one competency before starting this quiz mode.", tone: "error" });
      return;
    }
    if (["knowledge", "ability", "proficiency"].includes(mode) && !selectedProficiency) {
      setQuizStatus({ text: "Select a proficiency level before starting this quiz mode.", tone: "error" });
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
      const selectedEntry =
        proficiencyMappings.find((entry) => entry.proficiency_level === selectedProficiency) ||
        proficiencyMappings[0] ||
        null;
      const fallbackCompetency =
        selectedCompetency ||
        (selectedEntry?.knowledge_items?.length ? `knowledge: ${selectedEntry.knowledge_items[0]}` : "") ||
        (selectedEntry?.ability_items?.length ? `ability: ${selectedEntry.ability_items[0]}` : "") ||
        `${selectedSkill} skill quiz`;
      const proficiencyDesc = selectedEntry?.proficiency_description || "";

      const payload = {
        sector: selectedSector,
        skill: selectedSkill,
        competency: fallbackCompetency,
        proficiency_level: selectedProficiency || selectedEntry?.proficiency_level || "All proficiency levels",
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

  const resetPreviewResults = () => {
    setPreviewVideos([]);
    setHasPreviewedVideos(false);
    setPreviewSelection({});
    setPreviewMeta({
      query: "",
      alreadyIngestedCount: 0,
      quotaExceeded: false,
      quotaMessage: "",
      quota: null,
    });
    setPreviewBusy(false);
    setIngestBusy(false);
    setPreviewSessionUnits(0);
  };

  const clearPreviewFlow = () => {
    resetPreviewResults();
    setVideoFinderOpen(false);
    setIngestionStatus({ text: "", tone: "" });
  };

  const clearDirectSearchFlow = () => {
    setDirectSearchQuery("");
    setDirectSearchSource("library");
    setDirectResultsPage(1);
  };

  const resetDirectVideoSearchFlow = () => {
    resetPreviewResults();
    clearDirectSearchFlow();
  };

  const clearRecommendationFlow = () => {
    setRecommendations([]);
    setRecommendMeta(createEmptyRecommendMeta());
    setHasRecommended(false);
    setRecommendStatus({ text: "", tone: "" });
    setVotes({});
    setUserVotes({});
  };

  const resetSelectionPath = () => {
    setMapData(null);
    setSelectedProficiency("");
    setSelectedCompetency("");
    setExtraContext("");
    setTopK(6);
    setStrictSkillMatch(true);
    clearRecommendationFlow();
    clearPreviewFlow();
  };

  const openOverlayForSector = (sectorName, options = {}) => {
    if (!sectorName) {
      return;
    }

    const suggestedSkill = String(options.skill || "").trim();
    const isNewSector = sectorName !== selectedSector;
    const isReopeningOverlay = !overlayOpen;
    const shouldResetToSuggestedSkill =
      Boolean(suggestedSkill) && (suggestedSkill !== selectedSkill || isReopeningOverlay);

    setSelectedSector(sectorName);
    setOverlayOpen(true);
    setOverlayStep(suggestedSkill ? "competency" : "skills");
    setSkillSearch(suggestedSkill);
    setSkillStatus({
      text: suggestedSkill ? `Loading ${suggestedSkill}...` : "Loading skills...",
      tone: "",
    });
    setMapStatus({ text: "", tone: "" });
    setRecommendStatus({ text: "", tone: "" });
    setIngestionStatus({ text: "", tone: "" });

    if (suggestedSkill) {
      if (isNewSector || shouldResetToSuggestedSkill) {
        setSelectedSkill(suggestedSkill);
        setMapReloadKey((current) => current + 1);
        resetSelectionPath();
      }
    } else if (isNewSector) {
      setSelectedSkill("");
      resetSelectionPath();
    }
  };

  const openVideoFinder = () => {
    if (!selectedCompetency) {
      setIngestionStatus({ text: "Select one competency first.", tone: "error" });
      return;
    }
    setVideoFinderMode("path");
    resetPreviewResults();
    clearDirectSearchFlow();
    setVideoFinderOpen(true);
    setIngestionStatus({ text: "", tone: "" });
  };

  const openDirectVideoSearch = () => {
    setVideoFinderMode("direct");
    resetDirectVideoSearchFlow();
    setVideoFinderOpen(true);
    setIngestionStatus({ text: "If nothing fits, you can then search YouTube.", tone: "" });
  };

  const closeVideoFinder = () => setVideoFinderOpen(false);

  const loadAdminRequests = async (status = "pending") => {
    setAdminBusy(true);
    setAdminStatus({ text: "Loading admin review queue...", tone: "" });
    try {
      const params = new URLSearchParams({ status, limit: "100" });
      const response = await fetchJson(`/api/admin/video-mapping-requests?${params.toString()}`, {
        cache: "no-store",
      });
      const rows = Array.isArray(response.requests) ? response.requests : [];
      setAdminRequests(rows);
      setAdminStatus({
        text: rows.length ? `${rows.length} mapping request(s) loaded.` : "No mapping requests found.",
        tone: rows.length ? "success" : "",
      });
    } catch (error) {
      setAdminRequests([]);
      setAdminStatus({ text: `Unable to load admin review queue: ${error.message}`, tone: "error" });
    } finally {
      setAdminBusy(false);
    }
  };

  const openAdminTools = () => {
    setAdminOpen(true);
    setAdminTab("pending");
    setAdminSelectedRequest(null);
    setAdminMappings([]);
    loadAdminRequests("pending");
  };

  const closeAdminTools = () => {
    setAdminOpen(false);
    setAdminSelectedRequest(null);
    setAdminMappings([]);
    setAdminDirectVideos([]);
    setAdminDirectSelection({});
  };

  const openAdminRequest = async (videoId) => {
    if (!videoId) {
      return;
    }
    setAdminBusy(true);
    setAdminStatus({ text: "Loading video mapping request...", tone: "" });
    try {
      const response = await fetchJson(`/api/admin/video-mapping-requests/${encodeURIComponent(videoId)}`, {
        cache: "no-store",
      });
      const initialMappings = response.approved_mappings?.length
        ? response.approved_mappings
        : response.suggested_mappings?.length
          ? response.suggested_mappings
          : [createEmptyAdminMapping()];
      setAdminSelectedRequest(response);
      setAdminMappings(initialMappings.map((mapping) => ({ ...createEmptyAdminMapping(), ...mapping })));
      setAdminStatus({ text: "Review loaded. Edit mappings, then approve or reject.", tone: "success" });
    } catch (error) {
      setAdminStatus({ text: `Unable to load mapping request: ${error.message}`, tone: "error" });
    } finally {
      setAdminBusy(false);
    }
  };

  const updateAdminMapping = (index, field, value) => {
    setAdminMappings((current) =>
      current.map((mapping, mappingIndex) => (
        mappingIndex === index
          ? { ...mapping, [field]: value }
          : mapping
      )),
    );
  };

  const addAdminMapping = () => {
    setAdminMappings((current) => [...current, createEmptyAdminMapping()]);
  };

  const removeAdminMapping = (index) => {
    setAdminMappings((current) => current.filter((_, mappingIndex) => mappingIndex !== index));
  };

  const copySuggestedMappingsToEditor = () => {
    if (!adminSelectedRequest?.suggested_mappings?.length) {
      return;
    }
    setAdminMappings(adminSelectedRequest.suggested_mappings.map((mapping) => ({ ...createEmptyAdminMapping(), ...mapping })));
    setAdminStatus({ text: "Suggested mappings copied into the editor.", tone: "success" });
  };

  const approveAdminRequest = async () => {
    if (!adminSelectedRequest?.video_id) {
      setAdminStatus({ text: "Select a video request first.", tone: "error" });
      return;
    }
    const mappings = adminMappings.filter((mapping) =>
      mapping.sector && mapping.skill && mapping.proficiency_level && mapping.competency && mapping.item_type
    );
    if (!mappings.length) {
      setAdminStatus({ text: "Add at least one complete mapping before approval.", tone: "error" });
      return;
    }

    setAdminBusy(true);
    setAdminStatus({ text: "Approving and indexing video mappings...", tone: "" });
    try {
      const response = await fetchJson(
        `/api/admin/video-mapping-requests/${encodeURIComponent(adminSelectedRequest.video_id)}/approve`,
        {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reviewer: "admin-portal-prototype", mappings }),
        },
      );
      setAdminStatus({
        text: `${response.message} Indexed ${Number(response.embedding_indexed || 0)} mapping(s).`,
        tone: "success",
      });
      await loadAdminRequests("pending");
      setAdminSelectedRequest(null);
      setAdminMappings([]);
    } catch (error) {
      setAdminStatus({ text: `Unable to approve mappings: ${error.message}`, tone: "error" });
    } finally {
      setAdminBusy(false);
    }
  };

  const rejectAdminRequest = async () => {
    if (!adminSelectedRequest?.video_id) {
      setAdminStatus({ text: "Select a video request first.", tone: "error" });
      return;
    }
    setAdminBusy(true);
    setAdminStatus({ text: "Rejecting mapping request...", tone: "" });
    try {
      const response = await fetchJson(
        `/api/admin/video-mapping-requests/${encodeURIComponent(adminSelectedRequest.video_id)}/reject`,
        {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reviewer: "admin-portal-prototype", reason: "Rejected in temporary admin tools." }),
        },
      );
      setAdminStatus({ text: response.message || "Mapping request rejected.", tone: "success" });
      await loadAdminRequests("pending");
      setAdminSelectedRequest(null);
      setAdminMappings([]);
    } catch (error) {
      setAdminStatus({ text: `Unable to reject mapping request: ${error.message}`, tone: "error" });
    } finally {
      setAdminBusy(false);
    }
  };

  const searchAdminDirectVideos = async () => {
    const query = adminDirectQuery.trim();
    if (query.length < 2) {
      setAdminStatus({ text: "Enter an admin search prompt first.", tone: "error" });
      return;
    }
    setAdminBusy(true);
    setAdminStatus({ text: "Searching YouTube for admin curation...", tone: "" });
    try {
      const response = await fetchJson("/api/public/videos/search", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          max_results: 8,
          order: "relevance",
          source: "youtube",
        }),
      });
      const rows = Array.isArray(response.results) ? response.results : [];
      const nextSelection = {};
      rows.forEach((video) => {
        if (video.video_id && !video.already_ingested) {
          nextSelection[video.video_id] = true;
        }
      });
      setAdminDirectVideos(rows);
      setAdminDirectSelection(nextSelection);
      setAdminStatus({
        text: rows.length
          ? `${rows.length} video(s) found. Submit selected videos to create admin-review requests.`
          : "No YouTube videos matched this admin search.",
        tone: rows.length ? "success" : "error",
      });
    } catch (error) {
      setAdminDirectVideos([]);
      setAdminDirectSelection({});
      setAdminStatus({ text: `Unable to search YouTube: ${error.message}`, tone: "error" });
    } finally {
      setAdminBusy(false);
    }
  };

  const toggleAdminDirectSelection = (videoId) => {
    setAdminDirectSelection((current) => ({
      ...current,
      [videoId]: !current[videoId],
    }));
  };

  const ingestAdminDirectVideos = async () => {
    const selectedVideos = adminDirectVideos.filter((video) => adminDirectSelection[video.video_id]);
    if (!selectedVideos.length) {
      setAdminStatus({ text: "Select at least one admin search result first.", tone: "error" });
      return;
    }
    setAdminBusy(true);
    setAdminStatus({ text: "Submitting admin-selected videos for mapping review...", tone: "" });
    try {
      const response = await fetchJson("/api/public/videos/ingest", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          videos: selectedVideos.map((video) => ({
            sector: "",
            skill_name: "",
            competency: "",
            item_type: "",
            proficiency_level: "",
            proficiency_description: "",
            videoId: video.video_id,
            publishedAt: video.published_at,
            title: video.title,
            description: video.description,
            viewCount: Number(video.view_count || 0),
            likeCount: Number(video.like_count || 0),
            commentCount: Number(video.comment_count || 0),
            tags: Array.isArray(video.tags) ? video.tags : [],
            duration: video.duration || "",
            channelTitle: video.channel_title || "",
            thumbnailUrl: video.thumbnail_url || "",
            search_query: adminDirectQuery.trim(),
          })),
        }),
      });
      setAdminStatus({
        text: `${response.message} ${Number(response.pending_review_count || selectedVideos.length)} video(s) are ready in Pending Review.`,
        tone: "success",
      });
      setAdminDirectSelection({});
      await loadAdminRequests("pending");
      setAdminTab("pending");
    } catch (error) {
      setAdminStatus({ text: `Unable to submit admin-selected videos: ${error.message}`, tone: "error" });
    } finally {
      setAdminBusy(false);
    }
  };

  const closeOverlay = () => {
    setOverlayOpen(false);
    setOverlayStep("skills");
    setVideoFinderOpen(false);
    setVideoShortcutOpen(false);
  };

  const clearSectorSearch = () => setSectorSearch("");

  const onSelectSkill = (skillName) => {
    setSelectedSkill(skillName);
    setMapReloadKey((current) => current + 1);
    setOverlayStep("competency");
    setMapData(null);
    setSelectedProficiency("");
    setSelectedCompetency("");
    setCompetencySectionsOpen({ knowledge: false, ability: false });
    clearRecommendationFlow();
    clearPreviewFlow();
  };

  const onSelectProficiency = (proficiencyLevel) => {
    if (proficiencyLevel === selectedProficiency) {
      return;
    }
    setSelectedProficiency(proficiencyLevel);
    setSelectedCompetency("");
    setCompetencySectionsOpen({ knowledge: false, ability: false });
    clearRecommendationFlow();
    clearPreviewFlow();
  };

  const onSelectCompetency = (proficiencyLevel, competencyText) => {
    setSelectedProficiency(proficiencyLevel);
    setSelectedCompetency(competencyText);
    setOverlayStep("recommendations");
    clearRecommendationFlow();
    clearPreviewFlow();
  };

  const requestRecommendations = async (requestedStrictSkillMatch = strictSkillMatch) => {
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
    setRecommendations([]);
    setRecommendMeta(createEmptyRecommendMeta());
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
      strict_skill_match: requestedStrictSkillMatch,
    };

    try {
      const response = await fetchJson("/api/public/recommend/videos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const results = response.results || [];
      setRecommendations(results);
      const serverVotes = {};
      results.forEach((v) => { serverVotes[v.video_id] = v.user_votes || 0; });
      setVotes(serverVotes);
      setUserVotes({});
      setRecommendMeta({
        query: response.query || "",
        appliedFilters: response.applied_filters || {},
        count: Number(response.count || 0),
        usedStrictSkillMatch: requestedStrictSkillMatch,
      });
      if (Number(response.count || 0) > 0) {
        setRecommendStatus({ text: `${response.count} recommendation(s) ready.`, tone: "success" });
      } else if (requestedStrictSkillMatch) {
        setRecommendStatus({
          text: "No videos matched this competency under the strict skill filter.",
          tone: "warning",
        });
      } else {
        setRecommendStatus({
          text: "No videos matched this path. Try adding context or submitting more videos for review.",
          tone: "warning",
        });
      }
    } catch (error) {
      setRecommendMeta(createEmptyRecommendMeta());
      setRecommendStatus({ text: `Unable to recommend videos: ${error.message}`, tone: "error" });
    }
  };

  const recommendVideos = () => requestRecommendations(strictSkillMatch);

  const retryRecommendationWithoutStrictFilter = () => {
    setStrictSkillMatch(false);
    requestRecommendations(false);
  };

  // --- Vote helpers ---

  const castVote = async (videoId, direction) => {
    const current = userVotes[videoId] || 0;
    let serverDelta;
    let newUserVote;
    if (current === direction) {
      // Toggle off: undo previous vote
      serverDelta = -direction;
      newUserVote = 0;
    } else {
      // New vote (possibly switching direction)
      serverDelta = direction - current;
      newUserVote = direction;
    }
    // Optimistic UI update
    setUserVotes((prev) => ({ ...prev, [videoId]: newUserVote }));
    setVotes((prev) => ({ ...prev, [videoId]: (prev[videoId] || 0) + serverDelta }));
    try {
      const response = await fetchJson(`/api/public/videos/${encodeURIComponent(videoId)}/vote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vote: serverDelta }),
      });
      setVotes((prev) => ({ ...prev, [videoId]: response.votes }));
    } catch (_) { /* revert on failure */ }
  };

  const reorderByVotes = (videos, votesMap) => {
    const result = [...videos];
    let changed = true;
    while (changed) {
      changed = false;
      for (let i = 0; i < result.length - 1; i++) {
        const vUpper = votesMap[result[i].video_id] || 0;
        const vLower = votesMap[result[i + 1].video_id] || 0;
        if (vLower - vUpper >= 3) {
          [result[i], result[i + 1]] = [result[i + 1], result[i]];
          changed = true;
        }
      }
    }
    return result;
  };

  const displayedRecommendations = useMemo(
    () => reorderByVotes(recommendations, votes),
    [recommendations, votes],
  );

  const previewVideosForIngestion = async () => {
    if (!selectedSector || !selectedSkill) {
      setIngestionStatus({ text: "Choose an industry and skill first.", tone: "error" });
      return;
    }
    if (!selectedCompetency) {
      setIngestionStatus({ text: "Select one competency first.", tone: "error" });
      return;
    }

    setPreviewBusy(true);
    setHasPreviewedVideos(true);
    setIngestionStatus({ text: "Finding candidate videos to submit for review...", tone: "" });

    const payload = {
      sector: selectedSector,
      skill: selectedSkill,
      proficiency_level: selectedProficiency || null,
      competency: selectedCompetency,
      extra_context: extraContext.trim() || null,
    };

    try {
      const response = await fetchJson("/api/public/videos/preview", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const previewRows = Array.isArray(response.results) ? response.results : [];
      const nextSelection = {};
      previewRows.forEach((video) => {
        if (!video.already_ingested && video.video_id) {
          nextSelection[video.video_id] = true;
        }
      });

      setPreviewVideos(previewRows);
      setPreviewSelection(nextSelection);
      setPreviewMeta({
        query: response.query || "",
        alreadyIngestedCount: Number(response.already_ingested_count || 0),
        quotaExceeded: Boolean(response.quota_exceeded),
        quotaMessage: response.quota_message || "",
        quota: response.quota || null,
      });
      setPreviewSessionUnits((current) => current + Number(response.quota?.estimated_units || 0));

      if (!previewRows.length) {
        setIngestionStatus({
          text: response.quota_message || "No candidate videos matched this path.",
          tone: response.quota_exceeded ? "error" : "error",
        });
      } else if (Object.keys(nextSelection).length) {
        setIngestionStatus({
          text: response.quota_exceeded
            ? `${previewRows.length} candidate video(s) found before the YouTube credit limit was reached.`
            : `${previewRows.length} candidate video(s) ready. ${Object.keys(nextSelection).length} new video(s) preselected for review.`,
          tone: response.quota_exceeded ? "error" : "success",
        });
      } else {
        setIngestionStatus({
          text: response.quota_exceeded
            ? `${previewRows.length} candidate video(s) found, but the YouTube credit limit was reached during preview.`
            : `${previewRows.length} candidate video(s) found, but they are already in the library.`,
          tone: response.quota_exceeded ? "error" : "success",
        });
      }
    } catch (error) {
      const payload = error.payload || {};
      setPreviewVideos([]);
      setPreviewSelection({});
      setPreviewMeta({
        query: payload.query || "",
        alreadyIngestedCount: Number(payload.already_ingested_count || 0),
        quotaExceeded: Boolean(payload.quota_exceeded),
        quotaMessage: payload.quota_message || "",
        quota: payload.quota || null,
      });
      setIngestionStatus({
        text: error.status === 429
          ? error.message
          : `Unable to preview videos: ${error.message}`,
        tone: "error",
      });
    } finally {
      setPreviewBusy(false);
    }
  };

  const searchDirectYoutubeVideos = async (sourceOverride = "library") => {
    const query = directSearchQuery.trim();
    if (query.length < 2) {
      setIngestionStatus({ text: "Enter a search prompt first.", tone: "error" });
      return;
    }

    const searchSource = sourceOverride === "youtube" ? "youtube" : "library";
    setDirectSearchSource(searchSource);
    setDirectResultsPage(1);
    setPreviewBusy(true);
    setHasPreviewedVideos(true);
    setIngestionStatus({
      text: searchSource === "library" ? "Searching saved indexed videos..." : "Searching YouTube directly...",
      tone: "",
    });

    try {
      const response = await fetchJson("/api/public/videos/search", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          max_results: searchSource === "library" ? 80 : 8,
          order: "relevance",
          source: searchSource,
        }),
      });

      let previewRows = Array.isArray(response.results) ? response.results : [];
      let filteredSavedCount = Number(response.already_ingested_count || 0);
      if (searchSource === "youtube" && previewRows.length) {
        const libraryResponse = await fetchJson("/api/public/videos/search", {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query,
            max_results: 80,
            order: "relevance",
            source: "library",
          }),
        });
        const savedVideoIds = new Set(
          (Array.isArray(libraryResponse.results) ? libraryResponse.results : [])
            .map((video) => String(video.video_id || "").trim())
            .filter(Boolean),
        );
        if (savedVideoIds.size) {
          const originalCount = previewRows.length;
          previewRows = previewRows.filter((video) => !savedVideoIds.has(String(video.video_id || "").trim()));
          filteredSavedCount += originalCount - previewRows.length;
        }
      }
      const nextSelection = {};
      previewRows.forEach((video) => {
        if (video.video_id && searchSource === "youtube" && !video.already_ingested) {
          nextSelection[video.video_id] = true;
        }
      });

      setPreviewVideos(previewRows);
      setPreviewSelection(nextSelection);
      setPreviewMeta({
        query: response.query || query,
        alreadyIngestedCount: filteredSavedCount,
        quotaExceeded: Boolean(response.quota_exceeded),
        quotaMessage: response.quota_message || "",
        quota: response.quota || null,
      });
      if (searchSource === "youtube") {
        setPreviewSessionUnits((current) => current + Number(response.quota?.estimated_units || 0));
      }

      if (!previewRows.length) {
        setIngestionStatus({
          text: response.quota_message || (
            searchSource === "library"
              ? "No saved indexed videos matched this prompt."
              : filteredSavedCount
                ? "All matching YouTube videos are already in the saved library."
                : "No YouTube videos matched this prompt."
          ),
          tone: "error",
        });
      } else {
        setIngestionStatus({
          text: searchSource === "library"
            ? `${previewRows.length} saved video result(s) ready.`
            : `${previewRows.length} new YouTube result(s) ready. Submit to send AI-suggested mappings for admin review.`,
          tone: response.quota_exceeded ? "error" : "success",
        });
      }
    } catch (error) {
      const payload = error.payload || {};
      setPreviewVideos([]);
      setPreviewSelection({});
      setPreviewMeta({
        query: payload.query || query,
        alreadyIngestedCount: 0,
        quotaExceeded: Boolean(payload.quota_exceeded),
        quotaMessage: payload.quota_message || "",
        quota: payload.quota || null,
      });
      setIngestionStatus({
        text: error.status === 429
          ? error.message
          : searchSource === "library"
            ? `Unable to search saved videos: ${error.message}`
            : `Unable to search YouTube: ${error.message}`,
        tone: "error",
      });
    } finally {
      setPreviewBusy(false);
    }
  };

  const togglePreviewSelection = (videoId) => {
    if (!videoId) {
      return;
    }
    setPreviewSelection((current) => ({
      ...current,
      [videoId]: !current[videoId],
    }));
  };

  const selectAllPreviewVideos = () => {
    const nextSelection = {};
    previewVideos.forEach((video) => {
      if (!video.already_ingested && video.video_id) {
        nextSelection[video.video_id] = true;
      }
    });
    setPreviewSelection(nextSelection);
  };

  const clearPreviewSelection = () => {
    setPreviewSelection({});
  };

  const ingestSelectedPreviewVideos = async () => {
    const selectedVideos = previewVideos.filter((video) => previewSelection[video.video_id]);
    if (!selectedVideos.length) {
      setIngestionStatus({ text: "Select at least one new preview video first.", tone: "error" });
      return;
    }
    setIngestBusy(true);
    setIngestionStatus({
      text: videoFinderMode === "direct"
        ? "Submitting selected videos and preparing AI-suggested mappings for admin review..."
        : "Submitting selected videos...",
      tone: "",
    });

    const payload = {
      videos: selectedVideos.map((video) => ({
        sector: videoFinderMode === "direct" ? "" : video.sector,
        skill_name: videoFinderMode === "direct" ? "" : video.skill_name,
        competency: videoFinderMode === "direct" ? "" : video.competency,
        item_type: videoFinderMode === "direct" ? "" : video.item_type,
        proficiency_level: videoFinderMode === "direct" ? "" : video.proficiency_level,
        proficiency_description: videoFinderMode === "direct" ? "" : video.proficiency_description,
        videoId: video.video_id,
        publishedAt: video.published_at,
        title: video.title,
        description: video.description,
        viewCount: Number(video.view_count || 0),
        likeCount: Number(video.like_count || 0),
        commentCount: Number(video.comment_count || 0),
        tags: Array.isArray(video.tags) ? video.tags : [],
        duration: video.duration || "",
        channelTitle: video.channel_title || "",
        thumbnailUrl: video.thumbnail_url || "",
        search_query: videoFinderMode === "direct" ? (directSearchQuery.trim() || previewMeta.query || "") : "",
      })),
    };

    try {
      const response = await fetchJson("/api/public/videos/ingest", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const selectedIds = new Set(selectedVideos.map((video) => video.video_id));
      setPreviewVideos((current) =>
        current.map((video) => (
          selectedIds.has(video.video_id)
            ? { ...video, already_ingested: true }
            : video
        )),
      );
      setPreviewSelection({});
      setPreviewMeta((current) => ({
        ...current,
        alreadyIngestedCount: current.alreadyIngestedCount + selectedIds.size,
      }));
      const embeddingStatus = response.embedding_status || "skipped";
      const ingestErrorCount = Number(response.error_count || 0);
      const pendingReviewCount = Number(response.pending_review_count || 0);
      const submissionMessage = String(response.message || "Selected videos submitted successfully.")
        .replace(/ingested and indexed/gi, "submitted and indexed")
        .replace(/ingested with warnings/gi, "submitted with warnings")
        .replace(/ingested/gi, "submitted")
        .replace(/ingesting/gi, "submitting");
      setIngestionStatus({
        text: pendingReviewCount
          ? `${submissionMessage} ${pendingReviewCount} video(s) sent for admin review.`
          : `${submissionMessage} Indexed ${Number(response.embedding_indexed || 0)} video(s).`,
        tone: embeddingStatus === "failed" ? "error" : ingestErrorCount > 0 ? "warning" : "success",
      });
    } catch (error) {
      setIngestionStatus({ text: `Unable to submit selected videos: ${error.message}`, tone: "error" });
    } finally {
      setIngestBusy(false);
    }
  };

  const sectorSuggestionScores = useMemo(() => {
    const scores = new Map();
    skillSuggestions.forEach((suggestion, suggestionIndex) => {
      const baseScore = Number(suggestion.match_score || 0);
      suggestion.sectors.forEach((sectorItem, sectorIndex) => {
        const sectorName = sectorItem.sector || "";
        if (!sectorName) {
          return;
        }

        const rankingScore =
          baseScore -
          suggestionIndex * 0.08 -
          sectorIndex * 0.03 +
          Math.min(0.6, Number(sectorItem.mapped_proficiency_count || 0) * 0.02);
        const currentScore = scores.get(sectorName) || 0;
        if (rankingScore > currentScore) {
          scores.set(sectorName, rankingScore);
        }
      });
    });
    return scores;
  }, [skillSuggestions]);

  const visibleSectors = useMemo(() => {
    const sectorRows = sectors.map((sector, index) => ({
      ...sector,
      _originalIndex: index,
      _toneIndex: index % 6,
    }));

    if (!debouncedSectorSearch) {
      return sectorRows;
    }

    return sectorRows
      .map((sector) => {
        const match = scoreSectorMatch(debouncedSectorSearch, sector.sector);
        const suggestionScore = sectorSuggestionScores.get(sector.sector) || 0;
        return {
          ...sector,
          _searchScore: Math.max(match.score, suggestionScore),
          _sectorMatchScore: match.score,
          _skillSuggestionScore: suggestionScore,
        };
      })
      .filter((sector) => sector._sectorMatchScore >= 3 || sector._skillSuggestionScore > 0)
      .sort((left, right) => {
        const scoreDiff = (right._searchScore || 0) - (left._searchScore || 0);
        if (scoreDiff !== 0) {
          return scoreDiff;
        }

        const skillDiff = Number(right.skill_count || 0) - Number(left.skill_count || 0);
        if (skillDiff !== 0) {
          return skillDiff;
        }

        return left._originalIndex - right._originalIndex;
      });
  }, [sectors, debouncedSectorSearch, sectorSuggestionScores]);

  const bestSectorMatch = debouncedSectorSearch && visibleSectors.length ? visibleSectors[0] : null;

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

  const sectorSearchFeedback = useMemo(() => {
    if (!sectors.length) {
      return { text: "", tone: "" };
    }
    if (!debouncedSectorSearch) {
      return { text: "Search supports industry names, skill titles, and close spellings.", tone: "" };
    }
    if (!visibleSectors.length) {
      return { text: `No close industry matches for "${debouncedSectorSearch}".`, tone: "error" };
    }

    const noun = visibleSectors.length === 1 ? "industry" : "industries";
    const skillText = skillSuggestions.length
      ? ` ${skillSuggestions.length} mapped skill suggestion(s) found.`
      : "";
    const actionText = bestSectorMatch ? ` Top industry suggestion: ${bestSectorMatch.sector}. Press Enter to open it.` : "";
    return {
      text: `${visibleSectors.length} ${noun} shown.${skillText}${actionText}`,
      tone: "success",
    };
  }, [sectors.length, debouncedSectorSearch, visibleSectors, skillSuggestions.length, bestSectorMatch]);

  const selectionSummary = selectedSkill
    ? `${selectedSector} · ${selectedSkill}${selectedProficiency ? ` · ${selectedProficiency}` : ""}`
    : "No skill path selected yet.";

  const recommendationLoading = recommendStatus.text === "Generating recommendations...";
  const recommendationStep = overlayStep === "recommendations" ? 3 : overlayStep === "competency" ? 2 : 1;
  const canTakeQuiz = overlayStep !== "skills" && Boolean(selectedSkill);

  const formatCompetencyLabel = (value) => {
    const parts = String(value || "").split(":");
    return parts.length > 1 ? parts.slice(1).join(":").trim() : value;
  };

  const renderCompetencySection = (type, title, items) => {
    const open = Boolean(competencySectionsOpen[type]);
    const count = Array.isArray(items) ? items.length : 0;
    return html`
      <section className="competency-section">
        <button
          type="button"
          className="competency-section-toggle"
          onClick=${() => setCompetencySectionsOpen((current) => ({ ...current, [type]: !current[type] }))}
          aria-expanded=${open}
        >
          <span className="competency-section-title">${title}</span>
          <span className="competency-section-count">${count}</span>
        </button>
        ${open
          ? html`
              <div className="chip-row">
                ${count
                  ? items.map((item) => {
                      const text = `${type}: ${item}`;
                      const active =
                        selectedMapEntry?.proficiency_level === selectedProficiency &&
                        selectedCompetency === text;
                      return html`
                        <button
                          key=${`${type}-${selectedMapEntry?.proficiency_level || "level"}-${item}`}
                          type="button"
                          className=${`chip competency-chip ${active ? "active" : ""}`}
                          onClick=${() => onSelectCompetency(selectedMapEntry.proficiency_level, text)}
                          aria-pressed=${active}
                        >
                          <span className="chip-check" aria-hidden="true"></span>
                          <span>${formatCompetencyLabel(text)}</span>
                        </button>
                      `;
                    })
                  : html`<span className="inline-note">No mapped ${title.toLowerCase()}.</span>`}
              </div>
            `
          : null}
      </section>
    `;
  };

  const selectedPreviewCount = previewVideos.reduce(
    (count, video) => count + (previewSelection[video.video_id] ? 1 : 0),
    0,
  );
  const directPageSize = 8;
  const directResultCount = videoFinderMode === "direct" ? previewVideos.length : 0;
  const directTotalPages = Math.max(1, Math.ceil(directResultCount / directPageSize));
  const safeDirectResultsPage = Math.min(directResultsPage, directTotalPages);
  const directPageStart = (safeDirectResultsPage - 1) * directPageSize;
  const directVisibleVideos = videoFinderMode === "direct"
    ? previewVideos.slice(directPageStart, directPageStart + directPageSize)
    : previewVideos;
  const previewQuotaFeedback = useMemo(() => {
    if (videoFinderMode === "direct" && directSearchSource === "library") {
      return {
        text: "Saved library search uses the internal semantic index and does not consume YouTube API credits.",
        tone: "",
      };
    }
    const quota = previewMeta.quota;
    if (!quota) {
      return {
        text: "Each preview run uses a small fixed YouTube credit estimate under the portal defaults.",
        tone: "",
      };
    }

    const estimatedUnits = Number(quota.estimated_units || 0);
    const dailyLimit = Number(quota.daily_limit || 0);
    const remainingAfterRun = Number(quota.remaining_after_run || 0);
    const sessionText = previewSessionUnits
      ? ` Preview attempts this session: ${previewSessionUnits} estimated units.`
      : "";
    const baseText =
      `Current preview estimate: ${estimatedUnits} / ${dailyLimit} units. Remaining after this run: ${remainingAfterRun}.`
      + sessionText;

    if (previewMeta.quotaExceeded) {
      return {
        text: `${previewMeta.quotaMessage || "YouTube credit limit reached."} ${baseText}`.trim(),
        tone: "error",
      };
    }
    if (quota.level === "over_limit") {
      return {
        text: `${baseText} This is projected to exceed the daily credit limit.`,
        tone: "error",
      };
    }
    if (quota.level === "warning") {
      return {
        text: `${baseText} This is close to the daily credit limit.`,
        tone: "warning",
      };
    }
    return { text: baseText, tone: "success" };
  }, [previewMeta, previewSessionUnits, videoFinderMode, directSearchSource]);

  const youtubeQuotaResetNote =
    "YouTube daily credits reset at midnight Pacific Time. In Singapore, that is 3:00 PM during U.S. daylight saving time and 4:00 PM otherwise.";

  const appliedSkillFilter = String(recommendMeta.appliedFilters?.skill || "");
  const appliedProficiencyFilter = String(recommendMeta.appliedFilters?.proficiency_level || "");
  const strictRecommendationMiss =
    hasRecommended && !recommendations.length && Boolean(appliedSkillFilter) && recommendMeta.usedStrictSkillMatch;
  const strictFilterLabel = appliedProficiencyFilter
    ? `${appliedSkillFilter} at proficiency ${appliedProficiencyFilter}`
    : appliedSkillFilter;

  const handleSectorSearchKeyDown = (event) => {
    if (event.key !== "Enter" || !bestSectorMatch) {
      return;
    }
    event.preventDefault();
    openOverlayForSector(bestSectorMatch.sector);
  };

  const recommendationContent = recommendationLoading
    ? html`
        <div className="recommendation-loading" role="status" aria-live="polite">
          <div className="spinner"></div>
          <div>
            <p className="recommendation-empty-title">Finding the best matches...</p>
            <p className="recommendation-empty-copy">Recommendations are being generated for the selected path.</p>
          </div>
        </div>
      `
    : !selectedCompetency
      ? html`<p className="empty-note">Select a competency to continue.</p>`
      : !hasRecommended
        ? html`<p className="empty-note">Ready when you are. Generate recommendations for this competency.</p>`
        : displayedRecommendations.length
      ? displayedRecommendations.map((video) => {
          const url = video.video_id
            ? `https://www.youtube.com/watch?v=${encodeURIComponent(video.video_id)}`
            : "#";
          const voteTotal = votes[video.video_id] || 0;
          const userVote = userVotes[video.video_id] || 0;
          return html`
            <div
              key=${`${video.video_id}-${video.score}`}
              className="video-card"
            >
              <a
                className="video-card-link"
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
              </a>
              <div>
                <a
                  className="video-title-link"
                  href=${url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <p className="video-title">${video.title || "Untitled video"}</p>
                </a>
                <p className="video-meta">
                  ${(video.channel_title || "Unknown channel") +
                  " · " +
                  (video.duration || "n/a") +
                  " · score " +
                  Number(video.score).toFixed(3)}
                </p>
                <span className="video-chip">${video.skill_name || selectedSkill || "Skill"}</span>
                <div className="vote-controls">
                  <button
                    type="button"
                    className=${`vote-btn${userVote === 1 ? " vote-btn--active-up" : ""}`}
                    onClick=${(e) => { e.stopPropagation(); castVote(video.video_id, 1); }}
                    title="Upvote"
                  >▲</button>
                  <span className="vote-count">${voteTotal}</span>
                  <button
                    type="button"
                    className=${`vote-btn${userVote === -1 ? " vote-btn--active-down" : ""}`}
                    onClick=${(e) => { e.stopPropagation(); castVote(video.video_id, -1); }}
                    title="Downvote"
                  >▼</button>
                </div>
              </div>
            </div>
          `;
        })
      : strictRecommendationMiss
        ? html`
            <div className="empty-note recommendation-empty">
              <p className="recommendation-empty-title">
                No videos matched the strict ${strictFilterLabel || selectedSkill || "current skill"} filter.
              </p>
              <p className="recommendation-empty-copy">
                The portal stayed inside the current skill. Retry without the strict filter to widen results across
                ${selectedSector ? ` ${selectedSector}` : " the selected sector"}, or use Find Another Video to submit fresh options for review.
              </p>
              <div className="recommendation-empty-actions">
                <button
                  type="button"
                  className="utility-btn utility-btn-brand"
                  onClick=${retryRecommendationWithoutStrictFilter}
                >
                  Retry Without Strict Filter
                </button>
              </div>
            </div>
          `
        : html`
            <div className="empty-note recommendation-empty">
              <p className="recommendation-empty-title">No recommendations matched the current filters.</p>
              <p className="recommendation-empty-copy">
                Try adding more context, adjusting the selected competency, or open Find Another Video to submit more candidates for review.
              </p>
            </div>
          `;

  const directYoutubeSearchQuery = (previewMeta.query || directSearchQuery.trim()).trim();
  const directYoutubeFallbackButton = videoFinderMode === "direct" && directYoutubeSearchQuery
    ? html`
        <button
          type="button"
          className="utility-btn utility-btn-brand"
          onClick=${() => searchDirectYoutubeVideos("youtube")}
          disabled=${previewBusy || ingestBusy}
        >
          ${previewVideos.length
            ? "Not finding what you need? Search YouTube"
            : `Search YouTube for "${directYoutubeSearchQuery}"`}
        </button>
      `
    : null;

  const previewContent = !hasPreviewedVideos
    ? html`<p className="empty-note">Preview candidate videos if the current recommendations miss the mark.</p>`
    : previewVideos.length
      ? html`
          ${directVisibleVideos.map((video) => {
          const url = video.video_id
            ? `https://www.youtube.com/watch?v=${encodeURIComponent(video.video_id)}`
            : "#";
          const isSelected = Boolean(previewSelection[video.video_id]);
          const isLibraryResult = videoFinderMode === "direct" && directSearchSource === "library";
          const isDisabled = video.already_ingested || ingestBusy || isLibraryResult;
          const metrics = videoFinderMode === "direct"
            ? [
                video.channel_title || "Unknown channel",
                isLibraryResult && video.score ? `Match ${Number(video.score).toFixed(3)}` : "",
              ].filter(Boolean)
            : [
                video.channel_title || "Unknown channel",
                video.duration || "n/a",
                `${Number(video.view_count || 0).toLocaleString()} views`,
              ];
          return html`
            <article
              key=${`preview-${video.video_id}`}
              className=${`preview-card ${isSelected ? "active" : ""} ${video.already_ingested ? "ingested" : ""}`}
            >
              ${isLibraryResult
                ? html`<div className="preview-select preview-select-placeholder" aria-hidden="true"></div>`
                : html`
                    <div className="preview-select">
                      <input
                        type="checkbox"
                        checked=${isSelected}
                        disabled=${isDisabled}
                        onChange=${() => togglePreviewSelection(video.video_id)}
                        aria-label=${`Select ${video.title || "preview video"} for review`}
                      />
                    </div>
                  `}
              ${video.thumbnail_url
                ? html`
                    <img
                      className="preview-thumb"
                      src=${video.thumbnail_url}
                      alt=${video.title || "Preview thumbnail"}
                      loading="lazy"
                    />
                  `
                : html`<div className="preview-thumb"></div>`}
              <div className="preview-body">
                <div className="preview-card-top">
                  <p className="video-title">${video.title || "Untitled video"}</p>
                  <span className=${`preview-state-chip ${video.already_ingested ? "ingested" : isSelected ? "selected" : "fresh"}`}>
                    ${isLibraryResult ? "Saved library" : video.already_ingested ? "Already in library" : isSelected ? "Selected" : "New candidate"}
                  </span>
                </div>
                <p className="video-meta">${metrics.join(" · ")}</p>
                <p className="preview-desc">${video.description || "No description provided."}</p>
                <div className="preview-footer">
                  ${videoFinderMode === "direct"
                    ? html`<span></span>`
                    : html`<span className="video-chip">${video.skill_name || selectedSkill || "Skill"}</span>`}
                  <a
                    className="preview-link"
                    href=${url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Watch on YouTube
                  </a>
                </div>
              </div>
            </article>
          `;
        })}
          ${videoFinderMode === "direct" && directSearchSource === "library"
            ? html`<div className="preview-youtube-fallback">${directYoutubeFallbackButton}</div>`
            : null}
        `
      : videoFinderMode === "direct" && directSearchSource === "library"
        ? html`
            <div className="empty-note">
              <p>No saved indexed videos matched this prompt.</p>
              ${directYoutubeFallbackButton}
            </div>
          `
        : html`<p className="empty-note">No candidate videos matched the current path.</p>`;

  return html`
    <main className="academy-shell">
      <header className="hero">
        <div className="hero-brand">
          <img
            className="hero-logo"
            src="/academy/static/Blue Elephants Logo.png"
            alt="Blue Elephants Solutions logo"
          />
        </div>
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
        <div className="hero-actions">
          <div className="hero-lookup-cta">
            <a className="utility-btn utility-btn-brand hero-link" href="/job-roles">
              <span className="hero-link-icon" aria-hidden="true"></span>
              <span>Open job role lookup</span>
            </a>
            <p>Browse roles, work functions, and linked competencies.</p>
            <button
              type="button"
              className="utility-btn utility-btn-brand hero-link"
              onClick=${openDirectVideoSearch}
            >
              <span className="hero-video-icon" aria-hidden="true"></span>
              <span>Direct Video Search</span>
            </button>
            <p>Search saved library videos first, then fall back to YouTube only if needed.</p>
          </div>
        </div>
      </header>

      <section className="industry-stage">
        <div className="industry-head">
          <h2>Choose an Industry</h2>
          <p className="panel-status" data-tone=${sectorStatus.tone || undefined}>${sectorStatus.text}</p>
        </div>
        <div className="sector-search-panel">
          <div className="sector-search-copy">
            <label className="field-label" htmlFor="sector-search-input">Search industries or skills</label>
            <p className="sector-search-note">
              Type an industry, a skill title, or an approximate spelling to narrow the sector cards.
            </p>
          </div>
          <div className="sector-search-row">
            <input
              id="sector-search-input"
              type="search"
              placeholder="Try 'infocom', 'health care', 'data analysis', or 'finance'..."
              value=${sectorSearch}
              onInput=${(event) => setSectorSearch(event.target.value)}
              onKeyDown=${handleSectorSearchKeyDown}
              aria-label="Search industries or skills"
            />
            ${sectorSearch
              ? html`
                  <button type="button" className="utility-btn" onClick=${clearSectorSearch}>
                    Clear
                  </button>
                `
              : null}
          </div>
          <div className="sector-search-actions">
            <p className="panel-status" data-tone=${sectorSearchFeedback.tone || undefined}>
              ${sectorSearchFeedback.text}
            </p>
            ${bestSectorMatch
              ? html`
                  <button
                    type="button"
                    className="utility-btn utility-btn-brand"
                    onClick=${() => openOverlayForSector(bestSectorMatch.sector)}
                  >
                    Open best match
                  </button>
                `
              : null}
          </div>
          ${skillSuggestionStatus.text
            ? html`
                <p className="panel-status sector-search-substatus" data-tone=${skillSuggestionStatus.tone || undefined}>
                  ${skillSuggestionStatus.text}
                </p>
              `
            : null}
        </div>
        ${skillSuggestions.length
          ? html`
              <section className="skill-suggestion-panel" aria-label="Mapped skill suggestions">
                <div className="skill-suggestion-head">
                  <div>
                    <p className="field-label">Mapped skill suggestions</p>
                    <p className="skill-suggestion-note">
                      If you know the skill but not the industry, start from one of these mapped industry paths.
                    </p>
                  </div>
                </div>
                <div className="skill-suggestion-list">
                  ${skillSuggestions.map((suggestion) => html`
                    <article key=${suggestion.skill} className="skill-suggestion-card">
                      <div className="skill-suggestion-top">
                        <p className="skill-suggestion-title">${suggestion.skill}</p>
                        <span className="skill-suggestion-count">
                          ${suggestion.sector_count} ${suggestion.sector_count === 1 ? "industry" : "industries"}
                        </span>
                      </div>
                      <p className="skill-suggestion-meta">
                        ${suggestion.total_mapped_proficiency_count} mapped proficiency level(s)
                      </p>
                      <div className="skill-suggestion-chips">
                        ${suggestion.sectors.map((sectorItem) => html`
                          <button
                            key=${`${suggestion.skill}-${sectorItem.sector}`}
                            type="button"
                            className="skill-sector-chip"
                            onClick=${() => openOverlayForSector(sectorItem.sector, { skill: suggestion.skill })}
                          >
                            <span>${sectorItem.sector}</span>
                            <span className="skill-sector-chip-meta">
                              ${sectorItem.mapped_proficiency_count} level${sectorItem.mapped_proficiency_count === 1 ? "" : "s"}
                            </span>
                          </button>
                        `)}
                      </div>
                    </article>
                  `)}
                </div>
              </section>
            `
          : null}
        <div className="industry-grid">
          ${visibleSectors.length
            ? visibleSectors.map((sector) => html`
                <button
                  key=${sector.sector}
                  type="button"
                  className=${`sector-card tone-${sector._toneIndex} ${sector.sector === selectedSector ? "active" : ""}`}
                  onClick=${() => openOverlayForSector(sector.sector)}
                >
                  <div className="sector-top">
                    <p className="sector-title">${sector.sector}</p>
                    <span className="sector-count">${sector.skill_count} skills</span>
                  </div>
                  <p className="sector-cta">
                    ${bestSectorMatch && sector.sector === bestSectorMatch.sector
                      ? "Best match · open guided selector"
                      : "Open guided selector"}
                  </p>
                </button>
              `)
            : html`
                <p className="empty-note">
                  ${debouncedSectorSearch
                    ? "No industries match this search. Try fewer words or a shorter spelling."
                    : "No industries available."}
                </p>
              `}
        </div>
      </section>

      ${adminOpen
        ? html`
            <div className="admin-tools-backdrop" onClick=${closeAdminTools}>
              <section
                className="admin-tools-panel"
                role="dialog"
                aria-modal="true"
                aria-label="Temporary admin tools"
                onClick=${(event) => event.stopPropagation()}
              >
                <div className="admin-tools-head">
                  <div>
                    <p className="quiz-kicker">Temporary Admin Tools</p>
                    <h2>Video Mapping Review</h2>
                    <p className="selection-sub">Prototype panel for pending user ingests and admin-curated video search.</p>
                  </div>
                  <button type="button" className="close-btn" onClick=${closeAdminTools}>Close</button>
                </div>

                <div className="admin-tabs" role="tablist" aria-label="Admin tools tabs">
                  <button
                    type="button"
                    className=${`admin-tab ${adminTab === "pending" ? "active" : ""}`}
                    onClick=${() => {
                      setAdminTab("pending");
                      loadAdminRequests("pending");
                    }}
                  >
                    Pending Review
                  </button>
                  <button
                    type="button"
                    className=${`admin-tab ${adminTab === "direct" ? "active" : ""}`}
                    onClick=${() => setAdminTab("direct")}
                  >
                    Direct Search
                  </button>
                </div>

                <p className="panel-status" data-tone=${adminStatus.tone || undefined}>${adminStatus.text}</p>

                ${adminTab === "pending"
                  ? html`
                      <div className="admin-review-grid">
                        <aside className="admin-request-list">
                          <div className="admin-list-head">
                            <p className="selection-label">Requests</p>
                            <button
                              type="button"
                              className="utility-btn"
                              onClick=${() => loadAdminRequests("pending")}
                              disabled=${adminBusy}
                            >
                              Refresh
                            </button>
                          </div>
                          ${adminRequests.length
                            ? adminRequests.map((request) => html`
                                <button
                                  key=${request.video_id}
                                  type="button"
                                  className=${`admin-request-item ${adminSelectedRequest?.video_id === request.video_id ? "active" : ""}`}
                                  onClick=${() => openAdminRequest(request.video_id)}
                                  disabled=${adminBusy}
                                >
                                  <span className="admin-request-title">${request.title || request.video_id}</span>
                                  <span className="admin-request-meta">${request.channel_title || "Unknown channel"}</span>
                                  <span className="admin-request-status">${request.review_status}</span>
                                </button>
                              `)
                            : html`<p className="empty-note">No pending mapping requests yet.</p>`}
                        </aside>

                        <section className="admin-review-detail">
                          ${adminSelectedRequest
                            ? html`
                                <div className="admin-video-summary">
                                  ${adminSelectedRequest.thumbnail_url
                                    ? html`<img src=${adminSelectedRequest.thumbnail_url} alt=${adminSelectedRequest.title || "Video thumbnail"} />`
                                    : html`<div className="admin-video-thumb-placeholder"></div>`}
                                  <div>
                                    <p className="selection-label">${adminSelectedRequest.review_status}</p>
                                    <h3>${adminSelectedRequest.title || "Untitled video"}</h3>
                                    <p className="selection-sub">${adminSelectedRequest.channel_title || "Unknown channel"}</p>
                                    ${adminSelectedRequest.search_query
                                      ? html`<p className="admin-query">Search query: ${adminSelectedRequest.search_query}</p>`
                                      : null}
                                  </div>
                                </div>

                                <div className="admin-suggestion-box">
                                  <div className="admin-list-head">
                                    <p className="selection-label">AI Suggested Mapping</p>
                                    <button
                                      type="button"
                                      className="utility-btn"
                                      onClick=${copySuggestedMappingsToEditor}
                                      disabled=${!adminSelectedRequest.suggested_mappings?.length || adminBusy}
                                    >
                                      Use Suggestion
                                    </button>
                                  </div>
                                  ${adminSelectedRequest.suggested_mappings?.length
                                    ? adminSelectedRequest.suggested_mappings.map((mapping, index) => html`
                                        <div key=${`suggestion-${index}`} className="admin-suggestion-row">
                                          <strong>${mapping.sector} / ${mapping.skill} / Level ${mapping.proficiency_level}</strong>
                                          <span>${mapping.competency}</span>
                                          ${mapping.reason ? html`<small>${mapping.reason}</small>` : null}
                                        </div>
                                      `)
                                    : ["queued", "running"].includes(adminSelectedRequest.suggestion_status || "")
                                      ? html`<p className="empty-note">AI suggestion is being prepared. Refresh shortly or add mappings manually below.</p>`
                                      : (adminSelectedRequest.suggestion_status || "") === "failed"
                                        ? html`<p className="empty-note">AI suggestion failed${adminSelectedRequest.suggestion_error ? `: ${adminSelectedRequest.suggestion_error}` : "."} Add mappings manually below.</p>`
                                    : html`<p className="empty-note">No AI suggestion was found. Add mappings manually below.</p>`}
                                </div>

                                <div className="admin-mapping-editor">
                                  <div className="admin-list-head">
                                    <p className="selection-label">Approved Mappings</p>
                                    <button type="button" className="utility-btn" onClick=${addAdminMapping} disabled=${adminBusy}>
                                      Add Mapping
                                    </button>
                                  </div>
                                  ${adminMappings.map((mapping, index) => html`
                                    <div key=${`admin-map-${index}`} className="admin-mapping-row">
                                      <label className="field-label">
                                        Sector
                                        <input
                                          value=${mapping.sector}
                                          onInput=${(event) => updateAdminMapping(index, "sector", event.target.value)}
                                          placeholder="Exact seeded sector"
                                        />
                                      </label>
                                      <label className="field-label">
                                        Skill
                                        <input
                                          value=${mapping.skill}
                                          onInput=${(event) => updateAdminMapping(index, "skill", event.target.value)}
                                          placeholder="Exact seeded skill"
                                        />
                                      </label>
                                      <label className="field-label">
                                        Level
                                        <input
                                          value=${mapping.proficiency_level}
                                          onInput=${(event) => updateAdminMapping(index, "proficiency_level", event.target.value)}
                                          placeholder="3"
                                        />
                                      </label>
                                      <label className="field-label">
                                        Type
                                        <select
                                          value=${mapping.item_type}
                                          onChange=${(event) => updateAdminMapping(index, "item_type", event.target.value)}
                                        >
                                          <option value="knowledge">knowledge</option>
                                          <option value="ability">ability</option>
                                        </select>
                                      </label>
                                      <label className="field-label admin-competency-field">
                                        Competency
                                        <input
                                          value=${mapping.competency}
                                          onInput=${(event) => updateAdminMapping(index, "competency", event.target.value)}
                                          placeholder="knowledge: ..."
                                        />
                                      </label>
                                      <label className="field-label admin-description-field">
                                        Proficiency Description
                                        <textarea
                                          value=${mapping.proficiency_description}
                                          onInput=${(event) => updateAdminMapping(index, "proficiency_description", event.target.value)}
                                          rows="2"
                                        ></textarea>
                                      </label>
                                      <button
                                        type="button"
                                        className="utility-btn"
                                        onClick=${() => removeAdminMapping(index)}
                                        disabled=${adminBusy || adminMappings.length <= 1}
                                      >
                                        Remove
                                      </button>
                                    </div>
                                  `)}
                                </div>

                                <div className="admin-action-row">
                                  <button type="button" className="primary-btn" onClick=${approveAdminRequest} disabled=${adminBusy}>
                                    Approve & Index
                                  </button>
                                  <button type="button" className="utility-btn danger" onClick=${rejectAdminRequest} disabled=${adminBusy}>
                                    Reject
                                  </button>
                                </div>
                              `
                            : html`<p className="empty-note">Select a pending request to review AI suggested mappings.</p>`}
                        </section>
                      </div>
                    `
                  : html`
                      <section className="admin-direct-panel">
                        <div className="direct-search-row">
                          <input
                            type="search"
                            placeholder="Search YouTube as admin, e.g. AI ethics tutorial"
                            value=${adminDirectQuery}
                            onInput=${(event) => setAdminDirectQuery(event.target.value)}
                            onKeyDown=${(event) => {
                              if (event.key === "Enter") {
                                searchAdminDirectVideos();
                              }
                            }}
                          />
                          <button
                            type="button"
                            className="utility-btn utility-btn-brand"
                            onClick=${searchAdminDirectVideos}
                            disabled=${adminBusy}
                          >
                            ${adminBusy ? "Working..." : "Search YouTube"}
                          </button>
                        </div>
                        <div className="admin-direct-results">
                          ${adminDirectVideos.length
                            ? adminDirectVideos.map((video) => {
                                const selected = Boolean(adminDirectSelection[video.video_id]);
                                return html`
                                  <article key=${`admin-direct-${video.video_id}`} className=${`preview-card ${selected ? "active" : ""}`}>
                                    <div className="preview-select">
                                      <input
                                        type="checkbox"
                                        checked=${selected}
                                        onChange=${() => toggleAdminDirectSelection(video.video_id)}
                                        disabled=${adminBusy || video.already_ingested}
                                      />
                                    </div>
                                    ${video.thumbnail_url
                                      ? html`<img className="preview-thumb" src=${video.thumbnail_url} alt=${video.title || "Video thumbnail"} />`
                                      : html`<div className="preview-thumb"></div>`}
                                    <div className="preview-body">
                                      <div className="preview-card-top">
                                        <p className="video-title">${video.title || "Untitled video"}</p>
                                        <span className="preview-state-chip">${video.already_ingested ? "Already in library" : selected ? "Selected" : "New"}</span>
                                      </div>
                                      <p className="video-meta">${video.channel_title || "Unknown channel"}</p>
                                      <p className="preview-desc">${video.description || "No description provided."}</p>
                                    </div>
                                  </article>
                                `;
                              })
                            : html`<p className="empty-note">Search YouTube to curate new videos for mapping review.</p>`}
                        </div>
                        <div className="admin-action-row">
                          <button
                            type="button"
                            className="primary-btn"
                            onClick=${ingestAdminDirectVideos}
                            disabled=${adminBusy || !adminDirectVideos.some((video) => adminDirectSelection[video.video_id])}
                          >
                            Submit Selected To Pending Review
                          </button>
                        </div>
                      </section>
                    `}
              </section>
            </div>
          `
        : null}

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
                  ${canTakeQuiz
                    ? html`
                        <button
                          type="button"
                          className="primary-btn quiz-header-btn"
                          onClick=${openQuizModeSelector}
                        >
                          📝 Take Quiz
                        </button>
                      `
                    : html`<span></span>`}
                  <button type="button" className="close-btn" onClick=${closeOverlay}>Close</button>
                </div>

                <div className="flow-steps" aria-label="Recommendation flow">
                  ${[
                    { number: 1, label: "Choose Skill" },
                    { number: 2, label: "Select Competency" },
                    { number: 3, label: "Generate Recommendations" },
                  ].map((step) => html`
                    <div
                      key=${step.number}
                      className=${`flow-step ${recommendationStep === step.number ? "active" : ""} ${recommendationStep > step.number ? "complete" : ""}`}
                    >
                      <span className="flow-step-number">${step.number}</span>
                      <span className="flow-step-label">${step.label}</span>
                    </div>
                  `)}
                </div>

                <div className=${`overlay-grid wizard-grid wizard-${overlayStep}`}>
                  <aside className="picker-col">
                    <label className="field-label" htmlFor="skill-search">Find Skill</label>
                    <div className="search-input-wrap">
                      <input
                        id="skill-search"
                        type="search"
                        placeholder="Type to filter skills..."
                        value=${skillSearch}
                        onInput=${(event) => setSkillSearch(event.target.value)}
                      />
                    </div>
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
                                <div className="map-block-head">
                                  <div>
                                    <p className="selection-label">Step 2</p>
                                    <p className="selected-skill">Select a competency</p>
                                  </div>
                                  <div className="wizard-head-actions">
                                    <span className="selected-skill-pill">${selectedSkill}</span>
                                    <button
                                      type="button"
                                      className="wizard-back-btn"
                                      onClick=${() => setOverlayStep("skills")}
                                    >
                                      <span aria-hidden="true">←</span>
                                      Back to Skills
                                    </button>
                                  </div>
                                </div>
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

                                      <div className="competency-sections">
                                        ${renderCompetencySection(
                                          "knowledge",
                                          "Knowledge Competencies",
                                          selectedMapEntry.knowledge_items,
                                        )}
                                        ${renderCompetencySection(
                                          "ability",
                                          "Ability Competencies",
                                          selectedMapEntry.ability_items,
                                        )}
                                      </div>
                                    `
                                  : html`<p className="empty-note">Choose a skill to view mapped details.</p>`}
                              </div>
                            </div>

                            <aside className="result-panel">
                              <section className="recommend-card">
                                <div className="recommend-card-head">
                                  <p className="selection-label">Current Path</p>
                                  <button
                                    type="button"
                                    className="wizard-back-btn"
                                    onClick=${() => setOverlayStep("competency")}
                                  >
                                    <span aria-hidden="true">←</span>
                                    Back to Competencies
                                  </button>
                                </div>
                                <p className="selection-value">${selectionSummary}</p>
                                ${selectedCompetency
                                  ? html`<p className="selection-sub selected-path-competency">${formatCompetencyLabel(selectedCompetency)}</p>`
                                  : html`<p className="selection-sub">Select a competency to continue.</p>`}
                              </section>

                              <section className="recommend-card controls-card">
                                <div className="recommend-card-head">
                                  <p className="selection-label">Recommendation Settings</p>
                                </div>
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
                                  className="primary-btn recommend-primary-btn"
                                  onClick=${recommendVideos}
                                  disabled=${!selectedCompetency || recommendationLoading}
                                >
                                  <span className="video-btn-icon" aria-hidden="true"></span>
                                  ${recommendationLoading ? "Generating..." : "Generate Recommendations"}
                                </button>
                              </section>

                              <section className="recommend-card recommendation-results-card">
                                <div className="recommend-card-head">
                                  <p className="selection-label">Recommendation Results</p>
                                  ${hasRecommended && !recommendationLoading
                                    ? html`<span className="recommend-count">${displayedRecommendations.length}</span>`
                                    : null}
                                </div>
                                <p className="panel-status" data-tone=${recommendStatus.tone || undefined}>${recommendStatus.text}</p>
                                <div className="recommendation-list">${recommendationContent}</div>
                              </section>

                              <section className="recommend-card procurement-card">
                                <div className="procurement-head">
                                  <div>
                                    <p className="selection-label">Need More Videos?</p>
                                    <p className="selection-sub">Preview fresh candidates and submit them for admin review.</p>
                                  </div>
                                  <button
                                    type="button"
                                    className="utility-btn"
                                    onClick=${openVideoFinder}
                                    disabled=${!selectedCompetency}
                                  >
                                    Find Another Video
                                  </button>
                                  <button
                                    type="button"
                                    className="utility-btn utility-btn-brand"
                                    onClick=${openDirectVideoSearch}
                                  >
                                    Search Videos Directly
                                  </button>
                                </div>
                                ${hasPreviewedVideos
                                  ? html`
                                      <p className="panel-status" data-tone=${ingestionStatus.tone || undefined}>
                                        ${ingestionStatus.text || `Video finder ready. ${previewVideos.length} candidate video(s) in the latest preview.`}
                                      </p>
                                    `
                                  : html`
                                      <p className="panel-status">
                                        No preview run yet for this path.
                                      </p>
                                    `}
                              </section>
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

      ${videoFinderOpen
        ? html`
            <div className="video-finder-backdrop" onClick=${closeVideoFinder}>
              <section
                className="video-finder-panel"
                data-mode=${videoFinderMode}
                role="dialog"
                aria-modal="true"
                aria-label="Video finder"
                onClick=${(event) => event.stopPropagation()}
              >
                <div className="video-finder-head">
                  <div>
                    <p className="quiz-kicker">${videoFinderMode === "direct" ? "Direct Video Search" : "Video Finder"}</p>
                    <h2>${videoFinderMode === "direct" ? "Search Videos Directly" : selectedSkill || "Find More Videos"}</h2>
                    <p className="video-finder-path">
                      ${videoFinderMode === "direct" ? "Search YouTube for new videos, or search saved indexed videos semantically without selecting a skill first." : selectionSummary}
                      ${videoFinderMode === "direct" ? "" : selectedCompetency ? ` · ${selectedCompetency}` : ""}
                    </p>
                  </div>
                  <button type="button" className="close-btn" onClick=${closeVideoFinder}>Close</button>
                </div>

                <div className="video-finder-content">
                  ${videoFinderMode === "direct"
                    ? html`
                        <div className="direct-search-panel">
                          <label className="field-label" htmlFor="direct-youtube-query">Saved video search prompt</label>
                          <div className="direct-search-row">
                            <input
                              id="direct-youtube-query"
                              type="search"
                              placeholder="Example: simple tutorial for accountancy basics"
                              value=${directSearchQuery}
                              onInput=${(event) => setDirectSearchQuery(event.target.value)}
                              onKeyDown=${(event) => {
                                if (event.key === "Enter") {
                                  searchDirectYoutubeVideos();
                                }
                              }}
                            />
                            <button
                              type="button"
                              className="utility-btn utility-btn-brand"
                              onClick=${() => searchDirectYoutubeVideos("library")}
                              disabled=${previewBusy || ingestBusy}
                            >
                              ${previewBusy
                                ? "Searching..."
                                : hasPreviewedVideos
                                  ? "Search Library Again"
                                  : "Search Library"}
                            </button>
                          </div>
                        </div>
                      `
                    : null}
                  ${videoFinderMode === "direct"
                    ? null
                    : html`
                        <div className="video-finder-topbar">
                          <p className="selection-sub">
                            Search for fresh YouTube candidates using the current path, then submit the ones worth keeping for review.
                          </p>
                          <button
                            type="button"
                            className="utility-btn utility-btn-brand"
                            onClick=${previewVideosForIngestion}
                            disabled=${previewBusy || ingestBusy}
                          >
                            ${previewBusy ? "Finding videos..." : hasPreviewedVideos ? "Refresh Preview" : "Find Videos"}
                          </button>
                        </div>
                      `}

                  <p className="panel-status" data-tone=${previewQuotaFeedback.tone || undefined}>
                    ${previewQuotaFeedback.text}
                  </p>
                  <p className="video-finder-note">${youtubeQuotaResetNote}</p>

                  ${previewMeta.query
                    ? html`
                        <p className="preview-query">
                          Search query: <span>${previewMeta.query}</span>
                        </p>
                      `
                    : null}

                  ${previewVideos.length
                    ? html`
                        <div className="preview-toolbar">
                          <p className="preview-summary">
                            ${previewMeta.alreadyIngestedCount
                              ? `${previewMeta.alreadyIngestedCount} already in library`
                              : "All shown videos are new to the library"}
                            ${previewMeta.quotaExceeded ? " · quota limit reached during preview" : ""}
                          </p>
                          ${!(videoFinderMode === "direct" && directSearchSource === "library")
                            ? html`
                                <div className="preview-toolbar-actions">
                                  <button
                                    type="button"
                                    className="utility-btn"
                                    onClick=${selectAllPreviewVideos}
                                    disabled=${previewBusy || ingestBusy}
                                  >
                                    ${videoFinderMode === "direct" ? "Select all" : "Select new"}
                                  </button>
                                  <button
                                    type="button"
                                    className="utility-btn"
                                    onClick=${clearPreviewSelection}
                                    disabled=${!selectedPreviewCount || previewBusy || ingestBusy}
                                  >
                                    Clear
                                  </button>
                                </div>
                              `
                            : null}
                        </div>
                      `
                    : null}

                  <p className="panel-status" data-tone=${ingestionStatus.tone || undefined}>
                    ${ingestionStatus.text}
                  </p>
                  <div className="preview-list preview-list-modal">${previewContent}</div>
                  ${videoFinderMode === "direct" && previewVideos.length > directPageSize
                    ? html`
                        <div className="direct-results-pager">
                          <button
                            type="button"
                            className="utility-btn"
                            onClick=${() => setDirectResultsPage((page) => Math.max(1, page - 1))}
                            disabled=${safeDirectResultsPage <= 1 || previewBusy || ingestBusy}
                          >
                            Previous
                          </button>
                          <span>
                            Page ${safeDirectResultsPage} of ${directTotalPages}
                            · showing ${directPageStart + 1}-${Math.min(directPageStart + directPageSize, previewVideos.length)}
                            of ${previewVideos.length}
                          </span>
                          <button
                            type="button"
                            className="utility-btn"
                            onClick=${() => setDirectResultsPage((page) => Math.min(directTotalPages, page + 1))}
                            disabled=${safeDirectResultsPage >= directTotalPages || previewBusy || ingestBusy}
                          >
                            Next
                          </button>
                        </div>
                      `
                    : null}
                </div>

                ${!(videoFinderMode === "direct" && directSearchSource === "library")
                  ? html`
                      <div className="video-finder-footer">
                        <button
                          type="button"
                          className="primary-btn video-finder-ingest-btn"
                          onClick=${ingestSelectedPreviewVideos}
                          disabled=${!selectedPreviewCount || previewBusy || ingestBusy}
                        >
                          ${ingestBusy
                            ? "Submitting selected videos..."
                            : `Submit ${selectedPreviewCount} selected video${selectedPreviewCount === 1 ? "" : "s"} for review`}
                        </button>
                      </div>
                    `
                  : null}
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
                      className=${`quiz-mode-btn ${!selectedCompetency ? "disabled" : ""}`}
                      onClick=${() => loadQuiz("competency")}
                      disabled=${!selectedCompetency}
                    >
                      <p className="mode-title">Per Competency</p>
                      <p className="mode-desc">Quiz based on selected competency for this level</p>
                    </button>
                    <button
                      type="button"
                      className=${`quiz-mode-btn ${!selectedProficiency ? "disabled" : ""}`}
                      onClick=${() => loadQuiz("knowledge")}
                      disabled=${!selectedProficiency}
                    >
                      <p className="mode-title">Knowledge Only</p>
                      <p className="mode-desc">Quiz for Knowledge competencies for this level</p>
                    </button>
                    <button
                      type="button"
                      className=${`quiz-mode-btn ${!selectedProficiency ? "disabled" : ""}`}
                      onClick=${() => loadQuiz("ability")}
                      disabled=${!selectedProficiency}
                    >
                      <p className="mode-title">Ability Only</p>
                      <p className="mode-desc">Quiz for Ability competencies for this level</p>
                    </button>
                    <button
                      type="button"
                      className=${`quiz-mode-btn ${!selectedProficiency ? "disabled" : ""}`}
                      onClick=${() => loadQuiz("proficiency")}
                      disabled=${!selectedProficiency}
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

      <footer className="site-footer" aria-label="Site disclaimer">
        <p className="site-footer-copy">
          © 2026 Blue Elephants Solutions Pte. Ltd. For questions, please contact:${" "}
          <a href="mailto:contact@blue-elephants-solutions.com">contact@blue-elephants-solutions.com</a>.
        </p>
        <p className="site-footer-note">
          This prototype is for demonstration purposes only and shall be used at your own risk.
        </p>
      </footer>
    </main>
  `;
}

const rootElement = document.getElementById("academy-root");
const root = ReactDOM.createRoot(rootElement);
root.render(html`<${App} />`);
