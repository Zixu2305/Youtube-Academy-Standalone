(function () {
    "use strict";

    // ── Element references ────────────────────────────────────────
    const sectorSelect       = document.getElementById("q_sector_select");
    const searchInput        = document.getElementById("q_search_skills");
    const skillsContainer    = document.getElementById("q_skills_container");
    const competenciesContainer = document.getElementById("q_competencies_container");
    const proficiencyContainer  = document.getElementById("q_proficiency_container");
    const requirementsContainer = document.getElementById("q_requirements_container");
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
    const quizResults        = document.getElementById("q_quiz_results");
    const quizScoreBanner    = document.getElementById("q_quiz_score_banner");
    const quizReview         = document.getElementById("q_quiz_review");
    const retryQuizBtn       = document.getElementById("q_retry_quiz_btn");
    const resetQuizBtn       = document.getElementById("q_reset_quiz_btn");
    const newSelectionBtn    = document.getElementById("q_new_selection_btn");

    // ── Selection state ───────────────────────────────────────────
    let selectedSkill        = null;
    let selectedCompetency   = null;
    let selectedProficiency  = null;
    let selectedRequirement  = null;

    // ── Quiz state ────────────────────────────────────────────────
    let currentQuiz  = null;
    let userAnswers  = {};

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
            ["Competency",  selectedCompetency || "—"],
            ["Proficiency", selectedProficiency || "—"],
            ["Requirement", selectedRequirement || "—"],
        ];
        selectionDisplay.innerHTML = rows.map(([label, val]) => `
            <div class="quiz-context-row">
                <span class="quiz-ctx-label">${esc(label)}</span>
                <span class="quiz-ctx-val">${esc(val)}</span>
            </div>
        `).join("");
    }

    function updateTrigger() {
        const ready = !!(selectedSkill && selectedProficiency && selectedCompetency && selectedRequirement);
        quizTriggerArea.style.display = ready ? "block" : "none";
        updateSelectionSummary();
    }

    // ── Cascading loader helpers ──────────────────────────────────
    function clearBelow(level) {
        // level: "skill" | "proficiency" | "competency" | "requirement"
        if (level === "skill") {
            selectedSkill = null;
            skillsContainer.innerHTML = '<span class="cascade-hint">Select a sector above.</span>';
        }
        if (level === "skill") {
            selectedProficiency = null;
            proficiencyContainer.innerHTML = '<span class="cascade-hint">Select a skill above.</span>';
        }
        if (level === "skill" || level === "proficiency") {
            selectedCompetency = null;
            competenciesContainer.innerHTML = '<span class="cascade-hint">Select a proficiency level above.</span>';
        }
        if (level === "skill" || level === "proficiency" || level === "competency") {
            selectedRequirement = null;
            requirementsContainer.innerHTML = '<span class="cascade-hint">Select a competency above.</span>';
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
                clearBelow("proficiency");
                if (selectedSkill) loadProficiencyLevels();
            });
            row.appendChild(btn);
            skillsContainer.appendChild(row);
        });
    }

    // ── Competencies loader ───────────────────────────────────────
    async function loadCompetencies() {
        competenciesContainer.innerHTML = '<span class="cascade-hint">Loading…</span>';
        clearBelow("competency");
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
                clearBelow("competency");
                if (selectedCompetency) loadRequirements();
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
                clearBelow("competency");
                if (selectedProficiency) loadCompetencies();
            });
            row.appendChild(btn);
            proficiencyContainer.appendChild(row);
        });
    }

    // ── Requirements loader ───────────────────────────────────────
    async function loadRequirements() {
        requirementsContainer.innerHTML = '<span class="cascade-hint">Loading…</span>';
        selectedRequirement = null;
        updateTrigger();
        try {
            const res = await fetch("/get_requirement", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    sector: sectorSelect.value,
                    skill: selectedSkill,
                    proficiency_level: selectedProficiency,
                    competency: selectedCompetency,
                }),
            });
            const data = await res.json();
            renderRequirements(data.requirements || []);
        } catch {
            requirementsContainer.innerHTML = '<span class="cascade-hint cascade-error">Error loading requirements.</span>';
        }
    }

    function renderRequirements(reqs) {
        requirementsContainer.innerHTML = "";
        if (!reqs.length) {
            requirementsContainer.innerHTML = '<span class="cascade-hint">No requirements found.</span>';
            return;
        }
        reqs.forEach((req) => {
            const row = document.createElement("div");
            row.className = "requirement-item";
            const btn = document.createElement("span");
            btn.className = "requirement-name" + (selectedRequirement === req ? " selected" : "");
            btn.textContent = req;
            btn.addEventListener("click", () => {
                if (selectedRequirement === req) {
                    selectedRequirement = null;
                    btn.className = "requirement-name";
                } else {
                    document.querySelectorAll("#q_requirements_container .requirement-name.selected")
                        .forEach(el => el.classList.remove("selected"));
                    selectedRequirement = req;
                    btn.className = "requirement-name selected";
                }
                updateTrigger();
            });
            row.appendChild(btn);
            requirementsContainer.appendChild(row);
        });
        // Auto-select single option
        if (reqs.length === 1) {
            selectedRequirement = reqs[0];
            requirementsContainer.querySelector(".requirement-name").classList.add("selected");
            updateTrigger();
        }
    }

    // ── Sector / search wiring ────────────────────────────────────
    sectorSelect.addEventListener("change", async () => {
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

        questions.forEach((q) => {
            const userAns   = userAnswers[q.question_number];
            const isCorrect = userAns === q.correct;
            if (isCorrect) correct++;

            const rDiv = document.createElement("div");
            rDiv.className = `quiz-review-item ${isCorrect ? "quiz-review-correct" : "quiz-review-wrong"}`;

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
        showQuizPanel();
        resetQuizPanel();
        renderContextBanner();
        if (quizLoadingText) quizLoadingText.textContent = "Generating question 1 / 5\u2026";
        quizLoading.style.display = "flex";
        quizPanel.scrollIntoView({ behavior: "smooth" });

        currentQuiz = null;
        userAnswers = {};
        let collectedQuestions = [];
        let quizKey = null;

        try {
            const res = await fetch("/generate_quiz_stream", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    sector:                  sectorSelect.value,
                    skill:                   selectedSkill,
                    competency:              selectedCompetency,
                    proficiency_level:       selectedProficiency,
                    proficiency_description: selectedRequirement || "",
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
                        if (quizLoadingText && next <= 5) {
                            quizLoadingText.textContent = `Generating question ${next} / 5\u2026`;
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
            quizLoading.style.display = "none";
            quizError.textContent    = `Error: ${err.message}`;
            quizError.style.display  = "block";
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

    retryQuizBtn.addEventListener("click", () => {
        if (!currentQuiz) return;
        userAnswers = {};
        quizResults.style.display = "none";
        renderQuizQuestions(currentQuiz.questions);
    });

    resetQuizBtn.addEventListener("click", () => {
        currentQuiz = null;
        userAnswers = {};
        showQuizIdle();
    });

    newSelectionBtn.addEventListener("click", () => {
        currentQuiz = null;
        userAnswers = {};
        showQuizIdle();
        // Scroll back to the selection panel
        document.getElementById("q_sector_select").scrollIntoView({ behavior: "smooth" });
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
