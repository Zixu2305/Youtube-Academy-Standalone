(function () {
    "use strict";

    // ── Element references ────────────────────────────────────────
    const sectorSelect       = document.getElementById("q_sector_select");
    const searchInput        = document.getElementById("q_search_skills");
    const skillsContainer    = document.getElementById("q_skills_container");
    const competenciesContainer = document.getElementById("q_competencies_container");
    const proficiencyContainer  = document.getElementById("q_proficiency_container");
    const requirementsContainer = document.getElementById("q_requirements_container");
    const quizConfigArea     = document.getElementById("q_quiz_config_area");
    const quizTriggerArea    = document.getElementById("q_quiz_trigger_area");
    const takeQuizBtn        = document.getElementById("q_take_quiz_btn");
    const selectionSummary   = document.getElementById("q_selection_summary");
    const selectionDisplay   = document.getElementById("q_selection_display");

    const quizIdle           = document.getElementById("q_quiz_idle");
    const quizPanel          = document.getElementById("q_quiz_panel");
    const quizContextBanner  = document.getElementById("q_quiz_context_banner");
    const quizLoading        = document.getElementById("q_quiz_loading");
    const quizLoadingText    = document.getElementById("q_loading_text");
    const quizError          = document.getElementById("q_quiz_error");
    const quizQuestionsEl    = document.getElementById("q_quiz_questions");
    const quizActions        = document.getElementById("q_quiz_actions");
    const submitQuizBtn      = document.getElementById("q_submit_quiz_btn");
    const regenerateQuizBtn  = document.getElementById("q_regenerate_quiz_btn");
    const quizResults        = document.getElementById("q_quiz_results");
    const quizScoreBanner    = document.getElementById("q_quiz_score_banner");
    const quizReview         = document.getElementById("q_quiz_review");
    const retryQuizBtn       = document.getElementById("q_retry_quiz_btn");
    const resetQuizBtn       = document.getElementById("q_reset_quiz_btn");
    const newSelectionBtn    = document.getElementById("q_new_selection_btn");
    const storeQuizBtn       = document.getElementById("q_store_quiz_btn");
    const selectAllBtn       = document.getElementById("q_select_all_btn");
    const selectNoneBtn      = document.getElementById("q_select_none_btn");

    // ── Selection state ───────────────────────────────────────────
    let selectedSkill        = null;
    let selectedCompetency   = null;
    let selectedProficiency  = null;
    let selectedRequirement  = null;

    // ── Quiz configuration state ───────────────────────────────────
    let selectedQuestionTypes = new Set(["Conceptual", "Application", "Scenario-Based", "Technical", "Evaluation"]);
    let numQuestionsToGenerate = 5;

    // ── Quiz state ────────────────────────────────────────────────
    let currentQuiz  = null;
    let userAnswers  = {};
    let selectedQuestions = new Set();  // Track which questions are selected for storage
    let quizGenerationAbort = null;  // AbortController for canceling in-progress generation

    // ── Utilities ─────────────────────────────────────────────────
    function esc(text) {
        const v = String(text ?? "");
        return v
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    // ── Selection summary card ────────────────────────────────────
    function updateSelectionSummary() {
        if (!selectedSkill) {
            selectionSummary.style.display = "none";
            return;
        }
        selectionSummary.style.display = "block";
        const rows = [
            ["Sector",      sectorSelect.value],
            ["Skill",       selectedSkill],
            ["Proficiency", selectedProficiency || "—"],
            ["Requirement", selectedRequirement || "—"],
            ["Competency",  selectedCompetency || "—"],
        ];
        selectionDisplay.innerHTML = rows.map(([label, val]) => `
            <div class="quiz-context-row">
                <span class="quiz-ctx-label">${esc(label)}</span>
                <span class="quiz-ctx-val">${esc(val)}</span>
            </div>
        `).join("");
    }

    function updateTrigger() {
        const ready = !!(selectedSkill && selectedProficiency && selectedRequirement && selectedCompetency);
        quizConfigArea.style.display = ready ? "block" : "none";
        quizTriggerArea.style.display = ready ? "block" : "none";
        updateSelectionSummary();
    }

    // ── Cascading loader helpers ──────────────────────────────────
    function clearBelow(level) {
        // level: "skill" | "proficiency"
        // Clear all dependent selections below the given level
        if (level === "skill") {
            selectedProficiency = null;
            proficiencyContainer.innerHTML = '<span class="cascade-hint">Select a skill above.</span>';
        }
        if (level === "skill" || level === "proficiency") {
            selectedRequirement = null;
            requirementsContainer.innerHTML = '<span class="cascade-hint">Select a proficiency level above.</span>';
        }
        if (level === "skill" || level === "proficiency") {
            selectedCompetency = null;
            competenciesContainer.innerHTML = '<span class="cascade-hint">Select a proficiency level above.</span>';
        }
        updateTrigger();
    }

    // ── Skills loader ─────────────────────────────────────────────
    async function loadSkills(sector, searchTerm = "") {
        skillsContainer.innerHTML = '<span class="cascade-hint">Loading…</span>';
        try {
            const res = await fetch("/search_skills", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sector, search_term: searchTerm }),
            });
            const skills = await res.json();
            renderSkills(skills);
        } catch {
            skillsContainer.innerHTML = '<span class="cascade-hint cascade-error">Error loading skills.</span>';
        }
    }

    function renderSkills(skills) {
        skillsContainer.innerHTML = "";
        if (!skills.length) {
            skillsContainer.innerHTML = '<span class="cascade-hint">No skills found.</span>';
            return;
        }
        skills.forEach((skill) => {
            const row = document.createElement("div");
            row.className = "skill-item";
            const btn = document.createElement("span");
            btn.className = "skill-name" + (selectedSkill === skill ? " selected" : "");
            btn.textContent = skill;
            btn.addEventListener("click", () => {
                if (selectedSkill === skill) {
                    selectedSkill = null;
                    btn.className = "skill-name";
                } else {
                    document.querySelectorAll("#q_skills_container .skill-name.selected")
                        .forEach(el => el.classList.remove("selected"));
                    selectedSkill = skill;
                    btn.className = "skill-name selected";
                }
                clearBelow("skill");
                if (selectedSkill) loadProficiencyLevels();
            });
            row.appendChild(btn);
            skillsContainer.appendChild(row);
        });
    }

    async function loadCompetencies() {
        competenciesContainer.innerHTML = '<span class="cascade-hint">Loading…</span>';
        try {
            const res = await fetch("/search_competencies", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ 
                    sector: sectorSelect.value, 
                    skill: selectedSkill,
                    proficiency_level: selectedProficiency
                }),
            });
            const comps = await res.json();
            renderCompetencies(comps);
        } catch {
            competenciesContainer.innerHTML = '<span class="cascade-hint cascade-error">Error loading competencies.</span>';
        }
    }

    function renderCompetencies(comps) {
        competenciesContainer.innerHTML = "";
        if (!comps.length) {
            competenciesContainer.innerHTML = '<span class="cascade-hint">No competencies found.</span>';
            return;
        }
        comps.forEach((comp) => {
            const row = document.createElement("div");
            row.className = "competency-item";
            const btn = document.createElement("span");
            btn.className = "competency-name" + (selectedCompetency === comp ? " selected" : "");
            btn.textContent = comp;
            btn.addEventListener("click", () => {
                if (selectedCompetency === comp) {
                    selectedCompetency = null;
                    btn.className = "competency-name";
                } else {
                    document.querySelectorAll("#q_competencies_container .competency-name.selected")
                        .forEach(el => el.classList.remove("selected"));
                    selectedCompetency = comp;
                    btn.className = "competency-name selected";
                }
                updateTrigger();
            });
            row.appendChild(btn);
            competenciesContainer.appendChild(row);
        });
    }

    // ── Proficiency loader ────────────────────────────────────────
    async function loadProficiencyLevels() {
        proficiencyContainer.innerHTML = '<span class="cascade-hint">Loading…</span>';
        clearBelow("proficiency");
        try {
            const res = await fetch("/search_proficiency_levels", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    sector: sectorSelect.value,
                    skill: selectedSkill,
                }),
            });
            const levels = await res.json();
            renderProficiencyLevels(levels);
        } catch {
            proficiencyContainer.innerHTML = '<span class="cascade-hint cascade-error">Error loading levels.</span>';
        }
    }

    function renderProficiencyLevels(levels) {
        proficiencyContainer.innerHTML = "";
        if (!levels.length) {
            proficiencyContainer.innerHTML = '<span class="cascade-hint">No levels found.</span>';
            return;
        }
        levels.forEach((level) => {
            const row = document.createElement("div");
            row.className = "proficiency-item";
            const btn = document.createElement("span");
            btn.className = "proficiency-name" + (selectedProficiency === level ? " selected" : "");
            btn.textContent = level;
            btn.addEventListener("click", () => {
                if (selectedProficiency === level) {
                    selectedProficiency = null;
                    btn.className = "proficiency-name";
                } else {
                    document.querySelectorAll("#q_proficiency_container .proficiency-name.selected")
                        .forEach(el => el.classList.remove("selected"));
                    selectedProficiency = level;
                    btn.className = "proficiency-name selected";
                }
                clearBelow("proficiency");
                if (selectedProficiency) {
                    loadProficiencyDescription();
                    loadCompetencies();
                }
            });
            row.appendChild(btn);
            proficiencyContainer.appendChild(row);
        });
    }

    // ── Proficiency Description loader ───────────────────────────
    async function loadProficiencyDescription() {
        requirementsContainer.innerHTML = '<span class="cascade-hint">Loading…</span>';
        try {
            const res = await fetch("/get_requirement", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    sector: sectorSelect.value,
                    skill: selectedSkill,
                    proficiency_level: selectedProficiency,
                    competency: "", // empty for fetching description only
                }),
            });
            const data = await res.json();
            renderProficiencyDescription(data.requirements || []);
        } catch {
            requirementsContainer.innerHTML = '<span class="cascade-hint cascade-error">Error loading proficiency description.</span>';
        }
    }

    function renderProficiencyDescription(descriptions) {
        requirementsContainer.innerHTML = "";
        if (!descriptions.length || !descriptions[0]) {
            requirementsContainer.innerHTML = '<span class="cascade-hint">No description available.</span>';
            selectedRequirement = null;
            updateTrigger();
            return;
        }
        const description = descriptions[0];
        selectedRequirement = description;
        const row = document.createElement("div");
        row.className = "requirement-item";
        row.innerHTML = `<div class="requirement-text">${esc(description)}</div>`;
        requirementsContainer.appendChild(row);
        updateTrigger();
    }
    // ── Sector / search wiring ────────────────────────────────────
    sectorSelect.addEventListener("change", async () => {
        selectedSkill = null;
        clearBelow("skill");
        await loadSkills(sectorSelect.value);
    });

    searchInput.addEventListener("input", async () => {
        await loadSkills(sectorSelect.value, searchInput.value);
    });

    // ── Quiz UI helpers ───────────────────────────────────────────
    function showQuizIdle() {
        quizIdle.style.display = "flex";
        quizPanel.style.display = "none";
    }

    function showQuizPanel() {
        quizIdle.style.display = "none";
        quizPanel.style.display = "block";
    }

    function resetQuizPanel() {
        quizLoading.style.display = "none";
        quizError.style.display   = "none";
        quizQuestionsEl.innerHTML = "";
        quizActions.style.display = "none";
        quizResults.style.display = "none";
    }

    function renderContextBanner() {
        quizContextBanner.innerHTML = [
            ["Sector",      sectorSelect.value],
            ["Skill",       selectedSkill],
            ["Competency",  selectedCompetency],
            ["Proficiency", selectedProficiency],
            ["Requirement", selectedRequirement],
        ].map(([label, val]) => `
            <div class="quiz-context-row">
                <span class="quiz-ctx-label">${esc(label)}</span>
                <span class="quiz-ctx-val">${esc(val || "")}</span>
            </div>
        `).join("");
    }

    // ── Quiz question renderer ────────────────────────────────────
    function _buildQuestionEl(q) {
        const qDiv = document.createElement("div");
        qDiv.className = "quiz-question";

        if (q.question_type) {
            const badge = document.createElement("span");
            badge.className = "quiz-type-badge";
            badge.setAttribute("data-type", q.question_type.toLowerCase().replace(/[^a-z]/g, "-"));
            badge.textContent = q.question_type;
            qDiv.appendChild(badge);
        }

        const qText = document.createElement("p");
        qText.className = "quiz-question-text";
        qText.textContent = `${q.question_number}. ${q.question}`;
        qDiv.appendChild(qText);

        const optList = document.createElement("div");
        optList.className = "quiz-options";

        Object.entries(q.options).forEach(([label, text]) => {
            const optLabel = document.createElement("label");
            optLabel.className = "quiz-option";

            const radio = document.createElement("input");
            radio.type  = "radio";
            radio.name  = `quiz_q${q.question_number}`;
            radio.value = label;
            radio.addEventListener("change", () => {
                userAnswers[q.question_number] = label;
            });

            const optText = document.createElement("span");
            optText.className   = "quiz-option-label";
            optText.textContent = `${label}. ${text}`;

            optLabel.appendChild(radio);
            optLabel.appendChild(optText);
            optList.appendChild(optLabel);
        });

        qDiv.appendChild(optList);
        return qDiv;
    }

    function appendQuestion(q) {
        quizQuestionsEl.appendChild(_buildQuestionEl(q));
    }

    function renderQuizQuestions(questions) {
        userAnswers = {};
        quizQuestionsEl.innerHTML = "";
        questions.forEach((q) => quizQuestionsEl.appendChild(_buildQuestionEl(q)));
        quizLoading.style.display = "none";
        quizActions.style.display = "block";
        quizPanel.scrollIntoView({ behavior: "smooth" });
    }

    // ── Quiz results renderer ─────────────────────────────────────
    function renderQuizResults() {
        const questions = currentQuiz.questions;
        let correct = 0;
        quizReview.innerHTML = "";
        selectedQuestions.clear();  // Reset selected questions

        questions.forEach((q) => {
            const userAns   = userAnswers[q.question_number];
            const isCorrect = userAns === q.correct;
            if (isCorrect) correct++;

            const rDiv = document.createElement("div");
            rDiv.className = `quiz-review-item ${isCorrect ? "quiz-review-correct" : "quiz-review-wrong"}`;

            // Checkbox for selecting question to store
            const checkboxContainer = document.createElement("div");
            checkboxContainer.style.display = "flex";
            checkboxContainer.style.alignItems = "center";
            checkboxContainer.style.gap = "8px";
            checkboxContainer.style.marginBottom = "8px";
            
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.checked = true;  // Default to checked
            checkbox.id = `q_store_${q.question_number}`;
            checkbox.addEventListener("change", () => {
                if (checkbox.checked) {
                    selectedQuestions.add(q.question_number);
                } else {
                    selectedQuestions.delete(q.question_number);
                }
            });
            selectedQuestions.add(q.question_number);  // Add to selected by default

            const label = document.createElement("label");
            label.htmlFor = checkbox.id;
            label.style.margin = "0";
            label.style.cursor = "pointer";
            label.textContent = "Include in MongoDB";

            checkboxContainer.appendChild(checkbox);
            checkboxContainer.appendChild(label);
            rDiv.appendChild(checkboxContainer);

            // Question type badge in review
            if (q.question_type) {
                const badge = document.createElement("span");
                badge.className = "quiz-type-badge";
                badge.setAttribute("data-type", q.question_type.toLowerCase().replace(/[^a-z]/g, "-"));
                badge.textContent = q.question_type;
                rDiv.appendChild(badge);
            }

            const qText = document.createElement("p");
            qText.className   = "quiz-review-qtext";
            qText.textContent = `${q.question_number}. ${q.question}`;
            rDiv.appendChild(qText);

            Object.entries(q.options).forEach(([label, text]) => {
                const row         = document.createElement("div");
                row.className     = "quiz-review-option";
                const isUser      = label === userAns;
                const isCorrectOpt = label === q.correct;

                if (isCorrectOpt)            row.classList.add("quiz-opt-correct");
                if (isUser && !isCorrectOpt) row.classList.add("quiz-opt-wrong");

                const icon = isCorrectOpt ? "✓" : (isUser && !isCorrectOpt ? "✗" : " ");
                row.textContent = `${icon}  ${label}. ${text}`;
                rDiv.appendChild(row);
            });

            if (q.explanation) {
                const exp = document.createElement("p");
                exp.className   = "quiz-explanation";
                exp.textContent = `Explanation: ${q.explanation}`;
                rDiv.appendChild(exp);
            }

            quizReview.appendChild(rDiv);
        });

        const total = questions.length;
        const pct   = Math.round((correct / total) * 100);
        quizScoreBanner.className   = "quiz-score-banner " + (pct >= 80 ? "quiz-pass" : pct >= 60 ? "quiz-partial" : "quiz-fail");
        quizScoreBanner.textContent = `You scored ${correct} / ${total} (${pct}%)`;

        quizActions.style.display = "none";
        quizResults.style.display = "block";
        quizReview.scrollIntoView({ behavior: "smooth" });
    }

    // ── Generate quiz (streaming NDJSON) ──────────────────────────
    async function generateQuiz() {
        // Validate at least one question type is selected
        if (selectedQuestionTypes.size === 0) {
            alert("Please select at least one question type.");
            return;
        }
        
        // Validate number of questions is in valid range
        const numQuestionsInput = document.getElementById("q_num_questions");
        const numVal = parseInt(numQuestionsInput.value, 10);
        if (isNaN(numVal) || numVal < 5 || numVal > 15) {
            alert("Number of questions must be between 5 and 15. Please correct the value before generating.");
            return;
        }
        
        numQuestionsToGenerate = numVal;  // Update the state variable with the validated value
        
        // Abort any in-progress generation and start fresh
        if (quizGenerationAbort) {
            quizGenerationAbort.abort();
        }
        quizGenerationAbort = new AbortController();
        
        showQuizPanel();
        resetQuizPanel();
        renderContextBanner();
        if (quizLoadingText) quizLoadingText.textContent = "Generating question 1 / " + numVal + "\u2026";
        quizLoading.style.display = "flex";
        quizError.style.display = "none";  // Clear any previous errors
        quizPanel.scrollIntoView({ behavior: "smooth" });

        currentQuiz = null;
        userAnswers = {};
        selectedQuestions.clear();
        let collectedQuestions = [];
        let quizKey = null;

        try {
            const res = await fetch("/generate_quiz_stream", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                signal: quizGenerationAbort.signal,
                body: JSON.stringify({
                    sector:                  sectorSelect.value,
                    skill:                   selectedSkill,
                    competency:              selectedCompetency,
                    proficiency_level:       selectedProficiency,
                    proficiency_description: selectedRequirement || "",
                    question_types:          Array.from(selectedQuestionTypes),
                    num_questions:           numQuestionsToGenerate,
                }),
            });

            if (!res.ok) {
                throw new Error(`Server error ${res.status}`);
            }

            const reader  = res.body.getReader();
            const decoder = new TextDecoder();
            let   buffer  = "";

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                const lines = buffer.split("\n");
                buffer = lines.pop(); // keep incomplete last line

                for (const line of lines) {
                    const trimmed = line.trim();
                    if (!trimmed) continue;

                    let event;
                    try { event = JSON.parse(trimmed); }
                    catch { continue; }

                    if (event.type === "error") {
                        throw new Error(event.message || "Quiz generation failed.");
                    }
                    if (event.type === "context") {
                        quizKey = event.quiz_key;
                    }
                    if (event.type === "question") {
                        collectedQuestions.push(event);
                        appendQuestion(event);
                        const next = collectedQuestions.length + 1;
                        if (quizLoadingText && next <= numVal) {
                            quizLoadingText.textContent = `Generating question ${next} / ${numVal}\u2026`;
                        }
                    }
                    if (event.type === "done") {
                        quizKey = event.quiz_key;
                    }
                }
            }

            if (collectedQuestions.length === 0) {
                throw new Error("No questions received from server.");
            }

            currentQuiz = {
                quiz_key:   quizKey,
                questions:  collectedQuestions,
            };

            quizLoading.style.display = "none";
            quizActions.style.display = "block";

        } catch (err) {
            // Don't show error if this was an abort (user clicked to regenerate)
            if (err.name === "AbortError") {
                return;  // Silently exit, new generation is starting
            }
            quizLoading.style.display = "none";
            quizError.textContent    = `Error: ${err.message}`;
            quizError.style.display  = "block";
        }
    }

    // ── Regenerate quiz (skip cache) ────────────────────────────
    async function regenerateQuiz() {
        if (!currentQuiz) {
            alert("No quiz to regenerate.");
            return;
        }

        // Validate at least one question type is selected
        if (selectedQuestionTypes.size === 0) {
            alert("Please select at least one question type.");
            return;
        }
        
        // Validate number of questions is in valid range
        const numQuestionsInput = document.getElementById("q_num_questions");
        const numVal = parseInt(numQuestionsInput.value, 10);
        if (isNaN(numVal) || numVal < 5 || numVal > 15) {
            alert("Number of questions must be between 5 and 15. Please correct the value before regenerating.");
            return;
        }
        
        numQuestionsToGenerate = numVal;  // Update the state variable with the validated value

        // Confirm regeneration
        if (!confirm("This will generate new questions. Current answers will be lost. Continue?")) {
            return;
        }

        // Abort any in-progress generation and start fresh
        if (quizGenerationAbort) {
            quizGenerationAbort.abort();
        }
        quizGenerationAbort = new AbortController();

        showQuizPanel();
        resetQuizPanel();
        renderContextBanner();
        if (quizLoadingText) quizLoadingText.textContent = "Regenerating question 1 / " + numVal + "\u2026";
        quizLoading.style.display = "flex";
        quizError.style.display = "none";  // Clear any previous errors
        quizPanel.scrollIntoView({ behavior: "smooth" });

        currentQuiz = null;
        userAnswers = {};
        selectedQuestions.clear();
        let collectedQuestions = [];
        let quizKey = null;

        try {
            const res = await fetch("/generate_quiz_stream", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                signal: quizGenerationAbort.signal,
                body: JSON.stringify({
                    sector:                  sectorSelect.value,
                    skill:                   selectedSkill,
                    competency:              selectedCompetency,
                    proficiency_level:       selectedProficiency,
                    proficiency_description: selectedRequirement || "",
                    force_regenerate:        true,  // Skip cache, generate fresh questions
                    question_types:          Array.from(selectedQuestionTypes),
                    num_questions:           numQuestionsToGenerate,
                }),
            });

            if (!res.ok) {
                throw new Error(`Server error ${res.status}`);
            }

            const reader  = res.body.getReader();
            const decoder = new TextDecoder();
            let   buffer  = "";

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                const lines = buffer.split("\n");
                buffer = lines.pop();

                for (const line of lines) {
                    const trimmed = line.trim();
                    if (!trimmed) continue;

                    let event;
                    try { event = JSON.parse(trimmed); }
                    catch { continue; }

                    if (event.type === "error") {
                        throw new Error(event.message || "Quiz generation failed.");
                    }
                    if (event.type === "context") {
                        quizKey = event.quiz_key;
                    }
                    if (event.type === "question") {
                        collectedQuestions.push(event);
                        if (quizLoadingText) quizLoadingText.textContent = `Regenerating question ${collectedQuestions.length} / ${numVal}\u2026`;
                        appendQuestion(event);
                    }
                    if (event.type === "done") {
                        // Quiz generation complete
                    }
                }
            }

            if (collectedQuestions.length === 0) {
                throw new Error("No questions received from server.");
            }

            currentQuiz = {
                quiz_key:   quizKey,
                questions:  collectedQuestions,
            };

            quizLoading.style.display = "none";
            quizActions.style.display = "block";

        } catch (err) {
            // Don't show error if this was an abort (user clicked to regenerate again)
            if (err.name === "AbortError") {
                return;  // Silently exit, new regeneration is starting
            }
            quizLoading.style.display = "none";
            quizError.textContent    = `Error: ${err.message}`;
            quizError.style.display  = "block";
        }
    }

    // ── Store quiz to MongoDB ──────────────────────────────────────
    async function storeQuiz() {
        if (!currentQuiz) {
            alert("No quiz to store.");
            return;
        }

        // Collect only selected questions and remove question_number
        const selectedQuestionObjects = currentQuiz.questions
            .filter(q => selectedQuestions.has(q.question_number))
            .map(q => {
                const { question_number, ...qWithoutNumber } = q;
                return qWithoutNumber;
            });

        if (selectedQuestionObjects.length === 0) {
            alert("Please select at least one question to store.");
            return;
        }

        // Get competency item type from the competency string (format: "type: text")
        const competencyParts = selectedCompetency.split(": ");
        const itemType = competencyParts.length > 1 ? competencyParts[0].toLowerCase() : "knowledge";

        const payload = {
            sector:                  sectorSelect.value,
            skill:                   selectedSkill,
            competency:              selectedCompetency,
            proficiency_level:       selectedProficiency,
            proficiency_description: selectedRequirement || "",
            item_type:               itemType,
            questions:               selectedQuestionObjects,
        };

        storeQuizBtn.disabled = true;
        storeQuizBtn.textContent = "Storing...";

        try {
            const res = await fetch("/store_quiz", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            const result = await res.json();

            if (result.success) {
                alert(
                    `✓ Quiz stored successfully!\n` +
                    `Questions stored: ${selectedQuestionObjects.length}\n` +
                    `MongoDB IDs: ${result.mongo_ids.join(", ")}`
                );
                storeQuizBtn.textContent = "✓ Stored";
                storeQuizBtn.style.opacity = "0.6";
            } else {
                alert(`✗ Failed to store quiz:\n${result.message}`);
                storeQuizBtn.textContent = "💾 Store Quiz in MongoDB";
                storeQuizBtn.disabled = false;
            }
        } catch (err) {
            alert(`✗ Error storing quiz:\n${err.message}`);
            storeQuizBtn.textContent = "💾 Store Quiz in MongoDB";
            storeQuizBtn.disabled = false;
        }
    }

    // ── Button wiring ─────────────────────────────────────────────
    takeQuizBtn.addEventListener("click", generateQuiz);

    submitQuizBtn.addEventListener("click", () => {
        if (!currentQuiz) return;
        const total    = currentQuiz.questions.length;
        const answered = Object.keys(userAnswers).length;
        if (answered < total) {
            const rem = total - answered;
            if (!confirm(`${rem} question${rem > 1 ? "s" : ""} unanswered. Submit anyway?`)) return;
        }
        renderQuizResults();
    });

    regenerateQuizBtn.addEventListener("click", regenerateQuiz);

    retryQuizBtn.addEventListener("click", () => {
        if (!currentQuiz) return;
        userAnswers = {};
        selectedQuestions.clear();
        quizResults.style.display = "none";
        renderQuizQuestions(currentQuiz.questions);
    });

    resetQuizBtn.addEventListener("click", () => {
        currentQuiz = null;
        userAnswers = {};
        selectedQuestions.clear();
        showQuizIdle();
    });

    newSelectionBtn.addEventListener("click", () => {
        currentQuiz = null;
        userAnswers = {};
        selectedQuestions.clear();
        showQuizIdle();
        // Scroll back to the selection panel
        document.getElementById("q_sector_select").scrollIntoView({ behavior: "smooth" });
    });

    storeQuizBtn.addEventListener("click", storeQuiz);

    selectAllBtn.addEventListener("click", () => {
        if (!currentQuiz) return;
        currentQuiz.questions.forEach(q => selectedQuestions.add(q.question_number));
        // Update all checkboxes
        document.querySelectorAll('[id^="q_store_"]').forEach(cb => {
            cb.checked = true;
        });
    });

    selectNoneBtn.addEventListener("click", () => {
        if (!currentQuiz) return;
        selectedQuestions.clear();
        // Update all checkboxes
        document.querySelectorAll('[id^="q_store_"]').forEach(cb => {
            cb.checked = false;
        });
    });

    // ── Quiz configuration listeners ─────────────────────────────
    document.querySelectorAll(".q_question_type_cb").forEach(checkbox => {
        checkbox.addEventListener("change", () => {
            selectedQuestionTypes.clear();
            document.querySelectorAll(".q_question_type_cb").forEach(cb => {
                if (cb.checked) {
                    selectedQuestionTypes.add(cb.value);
                }
            });
            
            // Ensure at least one is selected
            if (selectedQuestionTypes.size === 0) {
                checkbox.checked = true;
                selectedQuestionTypes.add(checkbox.value);
            }
        });
    });

    document.getElementById("q_num_questions").addEventListener("input", (e) => {
        const val = parseInt(e.target.value, 10);
        const errorMsg = document.getElementById("q_num_questions_error");
        if (!isNaN(val) && val >= 5 && val <= 15) {
            numQuestionsToGenerate = val;
            errorMsg.style.display = "none";
        } else if (!isNaN(val)) {
            errorMsg.style.display = "block";
        }
    });

    // ── Bootstrap ─────────────────────────────────────────────────
    async function bootstrap() {
        showQuizIdle();
        if (sectorSelect.value) {
            await loadSkills(sectorSelect.value, "");
        } else {
            skillsContainer.innerHTML = '<span class="cascade-hint">No sectors found. Seed MySQL first.</span>';
        }
    }

    bootstrap();
})();
